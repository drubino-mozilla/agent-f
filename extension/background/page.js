/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const CONTENT_FILES = [
  "/vendor/readability/Readability.js",
  "/content/core.js",
  "/content/hooks.js",
  "/content/snapshot.js",
  "/content/input.js",
  "/content/read.js",
  "/content/main.js",
];

const NAVIGATED = Symbol("navigated");
const NOT_INJECTED = /Receiving end does not exist|Could not establish connection/i;
const DISCONNECTED = /message manager disconnected|Actor .* destroyed|context unloaded|page navigated|dead object|disconnected/i;

// Content scripts report refs as \u0001doc:id\u0001; the frame id is added here so the broker
// knows which frame each ref lives in.
function tagFrame(value, frameId) {
  if (typeof value === "string") {
    return value.replace(/\u0001([0-9a-f]+):(\d+)\u0001/g, `\u0001${frameId}:$1:$2\u0001`);
  }
  if (Array.isArray(value)) {
    return value.map(v => tagFrame(v, frameId));
  }
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = tagFrame(v, frameId);
    }
    return out;
  }
  return value;
}

// A fingerprint of the content files on disk. Pages whose copy doesn't match get the files again,
// which matters after an update or a development reload, since Firefox keeps old page scripts alive.
let contentBuild = null;
async function getContentBuild() {
  if (!contentBuild) {
    let hash = 0x811c9dc5;
    for (const file of CONTENT_FILES) {
      const text = await (await fetch(browser.runtime.getURL(file))).text();
      for (let i = 0; i < text.length; i++) {
        hash = Math.imul(hash ^ text.charCodeAt(i), 0x01000193) >>> 0;
      }
    }
    contentBuild = hash.toString(16);
  }
  return contentBuild;
}

async function inject(tabId, frameId) {
  try {
    const build = await getContentBuild();
    await browser.tabs.executeScript(tabId, { code: `var AgentFBuild = ${JSON.stringify(build)};`, frameId, runAt: "document_idle" });
    for (const file of CONTENT_FILES) {
      await browser.tabs.executeScript(tabId, { file, frameId, runAt: "document_idle" });
    }
  } catch (e) {
    throw new CommandError(
      "unreachable_page",
      `Agent F can't read or act in this page (${e.message}). Extensions are kept out of privileged pages such as about: and view-source:, and of Mozilla's restricted sites.`
    );
  }
}

async function sendToFrame(tabId, frameId, command, params) {
  const message = { agentF: true, command, params, build: await getContentBuild() };
  let reply;
  try {
    reply = await browser.tabs.sendMessage(tabId, message, { frameId });
  } catch (e) {
    if (!NOT_INJECTED.test(e.message)) {
      throw new CommandError(DISCONNECTED.test(e.message) ? "page_navigated" : "page_error", e.message);
    }
  }
  if (reply === undefined || (reply && !reply.ok && reply.error.code === "stale_content")) {
    await inject(tabId, frameId);
    try {
      reply = await browser.tabs.sendMessage(tabId, message, { frameId });
    } catch (e) {
      throw new CommandError("unreachable_page", `Agent F couldn't reach this page: ${e.message}`);
    }
  }
  if (!reply) {
    throw new CommandError("unreachable_page", "Agent F couldn't reach this page.");
  }
  if (!reply.ok) {
    throw new CommandError(reply.error.code, tagFrame(reply.error.message, frameId), tagFrame(reply.error.data, frameId));
  }
  return tagFrame(reply.result, frameId);
}

// Runs one page action and reports what it caused: navigation, new tabs, dialogs, focus.
async function runAction(p, command, params) {
  const effects = [];
  const tabId = p.tab;
  const frameId = p.frame || 0;
  await readyTab(tabId, effects);
  const before = await getTab(tabId);
  const nav = trackNavigation(tabId);
  const opened = [];
  const onCreated = t => {
    if (t.openerTabId === tabId) {
      opened.push(t.id);
    }
  };
  browser.tabs.onCreated.addListener(onCreated);
  const settleMs = p.settle_ms ?? 3000;
  let result = {};
  try {
    // If the action makes the page navigate, the old page may never send its reply,
    // so stop waiting once the new page has committed.
    const navigated = nav.wait(s => s.committed || s.closed, 60000).then(async ok => {
      if (ok === true) {
        await sleep(300);
      }
      return NAVIGATED;
    });
    try {
      const reply = await Promise.race([sendToFrame(tabId, frameId, command, params), navigated]);
      result = reply === NAVIGATED ? {} : reply || {};
    } catch (e) {
      if (!(e.code === "page_navigated" && nav.started)) {
        throw e;
      }
    }
    if (result.openInNewTab) {
      const tab = await createTab({ url: result.openInNewTab, windowId: before.windowId, openerTabId: tabId }, effects);
      opened.push(tab.id);
      effects.push(
        `The link opens in a new tab, so Agent F opened it in background tab ${tab.id}; the tab you clicked in stays where it was.`
      );
    }
    await sleep(80);
    if (nav.started) {
      const done = await nav.wait(s => s.complete || s.closed, settleMs + 12000);
      if (!done) {
        effects.push("The page was still loading when Agent F stopped waiting.");
      }
    } else {
      try {
        const settled = await sendToFrame(tabId, frameId, "settle", { max_ms: settleMs });
        effects.push(...settled.effects);
        if (!settled.settled) {
          effects.push(`The page was still changing after ${settleMs / 1000} s.`);
        }
      } catch (e) {
        if (nav.started) {
          await nav.wait(s => s.complete || s.closed, settleMs + 12000);
        }
      }
    }
  } finally {
    nav.stop();
    browser.tabs.onCreated.removeListener(onCreated);
  }
  let after;
  try {
    after = await tabInfo(tabId);
  } catch (e) {
    effects.push(`Tab ${tabId} closed.`);
    return { ...result, tab: summarizeTab(before), effects };
  }
  if (after.url !== before.url) {
    effects.push(`The tab navigated from ${before.url} to ${after.url}.`);
  }
  for (const id of opened) {
    if (result.openInNewTab) {
      continue;
    }
    try {
      const t = await browser.tabs.get(id);
      effects.push(`A new tab ${id} opened: ${t.url}.`);
    } catch (e) {}
  }
  delete result.openInNewTab;
  if (!result.target) {
    result.target = "the element (the page navigated before it could reply)";
  }
  return { ...result, tab: after, effects };
}

async function inlineFrames(tabId, text, p, depth) {
  const pattern = /\u0002(-?\d+):(\d+)\u0002/g;
  const matches = [...text.matchAll(pattern)];
  if (!matches.length) {
    return text;
  }
  let out = "";
  let last = 0;
  for (const m of matches) {
    out += text.slice(last, m.index);
    last = m.index + m[0].length;
    const frameId = Number(m[1]);
    const indent = Number(m[2]);
    const pad = "  ".repeat(indent);
    if (depth >= 3) {
      out += `${pad}- (nested frame not shown)`;
      continue;
    }
    try {
      const child = await sendToFrame(tabId, frameId, "snapshot", {
        mode: p.mode,
        viewport_only: p.viewport_only,
        max_chars: Math.max(1500, Math.floor((p.max_chars || 12000) / 4)),
        indent,
      });
      const inner = await inlineFrames(tabId, child.text, p, depth + 1);
      out += inner.trim() ? inner : `${pad}- (empty frame)`;
    } catch (e) {
      out += `${pad}- (frame content unavailable: ${e.message})`;
    }
  }
  return out + text.slice(last);
}

async function snapshotCommand(p) {
  const effects = [];
  await readyTab(p.tab, effects);
  const result = await sendToFrame(p.tab, p.frame || 0, "snapshot", { ...p, indent: 0 });
  result.text = await inlineFrames(p.tab, result.text, p, 0);
  return { ...result, tab: await tabInfo(p.tab), effects };
}

// Searches the top frame first, then every other frame, until the limit is reached.
async function findCommand(p) {
  const effects = [];
  await readyTab(p.tab, effects);
  const limit = p.limit || 10;
  let frames = [{ frameId: 0 }];
  try {
    frames = (await browser.webNavigation.getAllFrames({ tabId: p.tab })).sort((a, b) => a.frameId - b.frameId);
  } catch (e) {}
  const lines = [];
  let total = 0;
  for (const frame of frames) {
    try {
      const found = await sendToFrame(p.tab, frame.frameId, "find", { ...p, limit: Math.max(1, limit - lines.length) });
      total += found.total;
      if (found.text) {
        lines.push(...found.text.split("\n").slice(0, limit - lines.length));
      }
    } catch (e) {
      if (frame.frameId === 0) {
        throw e;
      }
    }
  }
  return { text: lines.join("\n"), total, tab: await tabInfo(p.tab), effects };
}

async function pageCommand(p, command) {
  const effects = [];
  await readyTab(p.tab, effects);
  const result = await sendToFrame(p.tab, p.frame || 0, command, p);
  return { ...result, tab: await tabInfo(p.tab), effects };
}

async function drawMarks(dataUrl, marks, format) {
  const img = new Image();
  img.src = dataUrl;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0);
  const k = img.naturalWidth / marks.viewport.width;
  const legend = [];
  const fontSize = Math.max(11, Math.round(12 * k));
  ctx.font = `bold ${fontSize}px sans-serif`;
  marks.marks.forEach((m, i) => {
    const n = String(i + 1);
    legend.push({ n: i + 1, token: m.token });
    ctx.lineWidth = Math.max(1, Math.round(1.5 * k));
    ctx.strokeStyle = "#ff3d00";
    ctx.strokeRect(m.x * k, m.y * k, m.w * k, m.h * k);
    const w = ctx.measureText(n).width + 6;
    const h = fontSize + 4;
    const x = Math.max(0, m.x * k - 1);
    const y = Math.max(0, m.y * k - h);
    ctx.fillStyle = "#ff3d00";
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(n, x + 3, y + fontSize);
  });
  return { dataUrl: canvas.toDataURL(`image/${format}`, 0.8), legend };
}

async function screenshotCommand(p) {
  const effects = [];
  await readyTab(p.tab, effects);
  const format = p.format === "png" ? "png" : "jpeg";
  const wantsElement = p.node !== undefined && p.node !== null ? true : !!p.selector;
  let geo = null;
  try {
    geo = await sendToFrame(p.tab, 0, "geometry", wantsElement ? p : {});
  } catch (e) {
    if (wantsElement) {
      throw e;
    }
  }
  let rect = null;
  if (wantsElement) {
    rect = geo.element;
  } else if (p.full_page && geo) {
    rect = { x: 0, y: 0, width: geo.page.width, height: Math.min(geo.page.height, 12000) };
    if (geo.page.height > 12000) {
      effects.push(`The page is ${geo.page.height} px tall; the screenshot stops at 12000 px.`);
    }
  }
  const cssWidth = rect ? rect.width : geo ? geo.viewport.width : null;
  const options = { format, quality: 80 };
  if (cssWidth) {
    options.scale = Math.min(geo.dpr || 1, (p.max_width || 1280) / cssWidth);
  }
  if (rect) {
    options.rect = rect;
  }
  let dataUrl;
  try {
    dataUrl = await browser.tabs.captureTab(p.tab, options);
  } catch (e) {
    throw new CommandError("capture_failed", `Firefox couldn't capture the tab: ${e.message}`);
  }
  let legend = [];
  if (p.marks && !rect && geo) {
    try {
      const marks = await sendToFrame(p.tab, 0, "marks", {});
      ({ dataUrl, legend } = await drawMarks(dataUrl, marks, format));
    } catch (e) {
      effects.push(`Couldn't draw marks: ${e.message}`);
    }
  }
  const comma = dataUrl.indexOf(",");
  return {
    image: dataUrl.slice(comma + 1),
    mime: `image/${format}`,
    legend,
    tab: await tabInfo(p.tab),
    effects,
  };
}

async function findInPageCommand(p) {
  const effects = [];
  await readyTab(p.tab, effects);
  const found = await browser.find.find(p.text, { tabId: p.tab, caseSensitive: !!p.case_sensitive });
  if (p.highlight && found.count) {
    await browser.find.highlightResults({ tabId: p.tab });
  } else {
    try {
      await browser.find.removeHighlighting(p.tab);
    } catch (e) {}
  }
  let snippets = [];
  try {
    ({ snippets } = await sendToFrame(p.tab, 0, "text_context", p));
  } catch (e) {}
  return { count: found.count, snippets, tab: await tabInfo(p.tab), effects };
}

const SERIALIZE = `function (value) {
  const seen = new WeakSet();
  const out = JSON.stringify(value, function (key, v) {
    if (v === undefined) return null;
    if (typeof v === "bigint") return v.toString();
    if (typeof v === "function") return "[Function " + (v.name || "anonymous") + "]";
    if (v && typeof v === "object") {
      if (typeof v.nodeType === "number" && typeof v.nodeName === "string") {
        return "[" + v.nodeName.toLowerCase() + (v.id ? "#" + v.id : "") + "]";
      }
      if (v instanceof Map) return Object.fromEntries(v);
      if (v instanceof Set) return Array.from(v);
      if (v instanceof Error) return String(v);
      if (seen.has(v)) return "[Circular]";
      seen.add(v);
    }
    return v;
  });
  return out === undefined ? "undefined" : out;
}`;

function evalSource(code, asExpression) {
  const body = asExpression ? `return (${code}\n);` : code;
  return `(async function () {
    const page = window.wrappedJSObject;
    const __value = await (async () => { ${body}\n })();
    return (${SERIALIZE})(__value);
  })().then(v => ({ ok: true, value: v }), e => ({ ok: false, error: String((e && e.stack) || e) }));`;
}

async function evalPageCommand(p) {
  const effects = [];
  await readyTab(p.tab, effects);
  const frameId = p.frame || 0;
  const run = async asExpression => {
    const [result] = await browser.tabs.executeScript(p.tab, {
      code: evalSource(p.code, asExpression),
      frameId,
      runAt: "document_idle",
    });
    return result;
  };
  let result;
  const timeout = (p.timeout || 10) * 1000;
  const withTimeout = promise =>
    Promise.race([
      promise,
      sleep(timeout).then(() => {
        throw new CommandError("timeout", `The code didn't finish within ${timeout / 1000} s.`);
      }),
    ]);
  try {
    result = await withTimeout(run(true));
  } catch (e) {
    if (e instanceof CommandError) {
      throw e;
    }
    // Runtime errors come back as { ok: false }; a thrown error means the code didn't compile
    // as an expression, so try it as statements.
    try {
      result = await withTimeout(run(false));
    } catch (e2) {
      if (e2 instanceof CommandError) {
        throw e2;
      }
      throw new CommandError("eval_failed", e2.message);
    }
  }
  if (!result || !result.ok) {
    throw new CommandError("script_error", result ? result.error : "No result.");
  }
  let value = result.value;
  const total = value.length;
  if (total > 20000) {
    value = value.slice(0, 20000);
  }
  return { value, total, tab: await tabInfo(p.tab), effects };
}

// Files for upload_file arrive from the broker in chunks, because Firefox limits each native
// message sent into the browser to 1 MB.
const uploads = new Map();

function base64ToBytes(data) {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function uploadChunkCommand(p) {
  const now = Date.now();
  for (const [id, u] of uploads) {
    if (now - u.created > 10 * 60 * 1000) {
      uploads.delete(id);
    }
  }
  let upload = uploads.get(p.upload);
  if (!upload) {
    upload = { name: p.name, type: p.type || "application/octet-stream", parts: [], created: now };
    uploads.set(p.upload, upload);
  }
  upload.parts[p.index] = base64ToBytes(p.data || "");
  return { received: p.index };
}

async function uploadFileCommand(p) {
  // Sent as plain bytes, which clone reliably into the page's content script; it builds the File.
  const files = (p.uploads || []).map(id => {
    const upload = uploads.get(id);
    if (!upload) {
      throw new CommandError("upload_missing", "The file data didn't arrive; try again.");
    }
    const size = upload.parts.reduce((n, part) => n + part.length, 0);
    const bytes = new Uint8Array(size);
    let at = 0;
    for (const part of upload.parts) {
      bytes.set(part, at);
      at += part.length;
    }
    return { name: upload.name, type: upload.type, bytes };
  });
  for (const id of p.uploads || []) {
    uploads.delete(id);
  }
  return runAction(p, "upload_file", { ...p, files });
}

// Requests in flight per tab, for wait_for network_idle.
const inflight = new Map();
const lastNetworkActivity = new Map();

function trackRequests() {
  const filter = { urls: ["<all_urls>"] };
  browser.webRequest.onBeforeRequest.addListener(d => {
    if (d.tabId < 0 || d.type === "websocket") {
      return;
    }
    if (!inflight.has(d.tabId)) {
      inflight.set(d.tabId, new Map());
    }
    inflight.get(d.tabId).set(d.requestId, Date.now());
    lastNetworkActivity.set(d.tabId, Date.now());
  }, filter);
  const finish = d => {
    const set = inflight.get(d.tabId);
    if (set) {
      set.delete(d.requestId);
    }
    lastNetworkActivity.set(d.tabId, Date.now());
  };
  browser.webRequest.onCompleted.addListener(finish, filter);
  browser.webRequest.onErrorOccurred.addListener(finish, filter);
  browser.tabs.onRemoved.addListener(tabId => {
    inflight.delete(tabId);
    lastNetworkActivity.delete(tabId);
  });
}

function networkIdle(tabId) {
  const now = Date.now();
  const pending = [...(inflight.get(tabId) || new Map()).values()].filter(start => now - start < 10000);
  return pending.length === 0 && now - (lastNetworkActivity.get(tabId) || 0) >= 500;
}

function globToRegExp(glob) {
  return new RegExp(glob.split("*").map(s => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*"));
}

async function waitForCommand(p) {
  const effects = [];
  await readyTab(p.tab, effects);
  const timeout = Math.min(60, p.timeout || 10) * 1000;
  const start = Date.now();
  let met = false;
  let what;
  if (p.load) {
    what = "the page to finish loading";
    met = (await waitForTabComplete(p.tab, timeout)) === "complete";
  } else {
    const urlTest = p.url ? (p.url.includes("*") ? globToRegExp(p.url) : null) : null;
    what = p.url
      ? `the address to match ${p.url}`
      : p.network_idle
        ? "the network to go quiet"
        : p.text
          ? `the text ${JSON.stringify(p.text)}`
          : p.text_gone
            ? `the text ${JSON.stringify(p.text_gone)} to go away`
            : p.gone
              ? "the element to go away"
              : "the element to appear";
    while (Date.now() - start < timeout) {
      if (p.url) {
        const tab = await getTab(p.tab);
        met = urlTest ? urlTest.test(tab.url) : tab.url.includes(p.url);
      } else if (p.network_idle) {
        met = networkIdle(p.tab);
      } else {
        try {
          met = (await sendToFrame(p.tab, p.frame || 0, "check", p)).met;
        } catch (e) {
          if (e.code === "stale_document") {
            throw e;
          }
          met = false;
        }
      }
      if (met) {
        break;
      }
      await sleep(250);
    }
  }
  return {
    met,
    what,
    waited: Math.round((Date.now() - start) / 100) / 10,
    tab: await tabInfo(p.tab),
    effects,
  };
}
