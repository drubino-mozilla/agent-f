/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

// Network and console capture, opt-in per tab (set_capture). Kept per tab in the background page
// so it survives navigations.

const MAX_CAPTURED = 500;
const MAX_BODY_BYTES_PER_TAB = 20 * 1024 * 1024;
const TEXT_TYPES = /json|text|xml|javascript|x-www-form-urlencoded|graphql/i;
const SECRET_KEYS = /pass(word|wd)?|pwd|secret|token|otp|pin\b|credential|auth/i;
const LEVELS = { debug: 0, log: 1, info: 1, warn: 2, error: 3 };

const captures = new Map();

function captureFor(tabId) {
  if (!captures.has(tabId)) {
    captures.set(tabId, {
      network: false,
      console: false,
      bodies: true,
      maxBody: 512 * 1024,
      pending: new Map(),
      requests: [],
      requestSeq: 0,
      bodyBytes: 0,
      messages: [],
      messageSeq: 0,
    });
  }
  return captures.get(tabId);
}

function redactForm(formData) {
  const out = {};
  for (const [key, values] of Object.entries(formData)) {
    out[key] = SECRET_KEYS.test(key) ? "redacted" : values.length === 1 ? values[0] : values;
  }
  return JSON.stringify(out);
}

function redactText(text) {
  return text
    .replace(/("[^"]*(?:pass(?:word|wd)?|pwd|secret|token|otp|credential)[^"]*"\s*:\s*)"(?:[^"\\]|\\.)*"/gi, '$1"redacted"')
    .replace(/((?:^|&)[^=&]*(?:pass(?:word|wd)?|pwd|secret|token|otp)[^=&]*=)[^&]*/gi, "$1redacted");
}

function requestBodyText(requestBody, limit) {
  if (!requestBody) {
    return null;
  }
  if (requestBody.formData) {
    return redactForm(requestBody.formData).slice(0, limit);
  }
  if (requestBody.raw && requestBody.raw.length) {
    const parts = requestBody.raw.filter(r => r.bytes).map(r => new Uint8Array(r.bytes));
    const size = parts.reduce((n, p) => n + p.length, 0);
    const joined = new Uint8Array(Math.min(size, limit));
    let at = 0;
    for (const p of parts) {
      if (at >= joined.length) {
        break;
      }
      joined.set(p.subarray(0, joined.length - at), at);
      at += p.length;
    }
    return redactText(new TextDecoder("utf-8", { fatal: false }).decode(joined));
  }
  return null;
}

function keep(list, entry) {
  list.push(entry);
  if (list.length > MAX_CAPTURED) {
    list.splice(0, list.length - MAX_CAPTURED);
  }
}

function freeBodies(state) {
  for (const entry of state.requests) {
    if (state.bodyBytes <= MAX_BODY_BYTES_PER_TAB) {
      break;
    }
    if (entry.bodyChunks) {
      state.bodyBytes -= entry.bodyStored;
      entry.bodyChunks = null;
      entry.bodyDropped = true;
    }
  }
}

function onBeforeRequest(d) {
  const state = captures.get(d.tabId);
  if (!state || !state.network) {
    return {};
  }
  const entry = {
    seq: ++state.requestSeq,
    requestId: d.requestId,
    method: d.method,
    url: d.url,
    type: d.type,
    started: d.timeStamp,
    requestBody: requestBodyText(d.requestBody, 16 * 1024),
  };
  state.pending.set(d.requestId, entry);
  keep(state.requests, entry);
  if (state.bodies && d.type === "xmlhttprequest") {
    try {
      const filter = browser.webRequest.filterResponseData(d.requestId);
      const chunks = [];
      let size = 0;
      filter.ondata = e => {
        if (size < state.maxBody) {
          const piece = e.data.slice(0, state.maxBody - size);
          chunks.push(piece);
          state.bodyBytes += piece.byteLength;
        }
        size += e.data.byteLength;
        filter.write(e.data);
      };
      filter.onstop = () => {
        filter.close();
        entry.bodyChunks = chunks;
        entry.bodyStored = chunks.reduce((n, c) => n + c.byteLength, 0);
        entry.responseSize = size;
        entry.truncated = size > state.maxBody;
        freeBodies(state);
      };
      filter.onerror = () => {};
    } catch (e) {}
  }
  return {};
}

function onCompleted(d) {
  const state = captures.get(d.tabId);
  const entry = state && state.pending.get(d.requestId);
  if (!entry) {
    return;
  }
  state.pending.delete(d.requestId);
  entry.status = d.statusCode;
  entry.duration = Math.round(d.timeStamp - entry.started);
  entry.fromCache = d.fromCache;
  const type = (d.responseHeaders || []).find(h => h.name.toLowerCase() === "content-type");
  entry.contentType = type ? type.value : "";
}

function onErrorOccurred(d) {
  const state = captures.get(d.tabId);
  const entry = state && state.pending.get(d.requestId);
  if (!entry) {
    return;
  }
  state.pending.delete(d.requestId);
  entry.error = d.error;
  entry.duration = Math.round(d.timeStamp - entry.started);
}

let networkListening = false;

function updateNetworkListeners() {
  const wanted = [...captures.values()].some(c => c.network);
  const filter = { urls: ["<all_urls>"] };
  if (wanted && !networkListening) {
    browser.webRequest.onBeforeRequest.addListener(onBeforeRequest, filter, ["blocking", "requestBody"]);
    browser.webRequest.onCompleted.addListener(onCompleted, filter, ["responseHeaders"]);
    browser.webRequest.onErrorOccurred.addListener(onErrorOccurred, filter);
    networkListening = true;
  } else if (!wanted && networkListening) {
    browser.webRequest.onBeforeRequest.removeListener(onBeforeRequest);
    browser.webRequest.onCompleted.removeListener(onCompleted);
    browser.webRequest.onErrorOccurred.removeListener(onErrorOccurred);
    networkListening = false;
  }
}

async function allFrames(tabId) {
  try {
    return await browser.webNavigation.getAllFrames({ tabId });
  } catch (e) {
    return [{ frameId: 0 }];
  }
}

async function setCaptureCommand(p) {
  await getTab(p.tab);
  const state = captureFor(p.tab);
  if (p.network !== undefined && p.network !== null) {
    state.network = !!p.network;
  }
  if (p.console !== undefined && p.console !== null) {
    state.console = !!p.console;
  }
  if (p.bodies !== undefined && p.bodies !== null) {
    state.bodies = !!p.bodies;
  }
  if (p.max_body) {
    state.maxBody = Math.max(1024, Math.min(5 * 1024 * 1024, p.max_body));
  }
  updateNetworkListeners();
  const effects = [];
  if (state.console) {
    for (const frame of await allFrames(p.tab)) {
      try {
        await sendToFrame(p.tab, frame.frameId, "enable_console", {});
      } catch (e) {
        if (frame.frameId === 0) {
          effects.push(`Console capture will start on the next page load here (${e.message}).`);
        }
      }
    }
  }
  return {
    network: state.network,
    console: state.console,
    bodies: state.bodies,
    maxBody: state.maxBody,
    tab: await tabInfo(p.tab),
    effects,
  };
}

function statusMatches(status, want) {
  if (want === undefined || want === null || want === "") {
    return true;
  }
  const text = String(want);
  if (/^\dxx$/i.test(text)) {
    return status !== undefined && String(status)[0] === text[0];
  }
  return String(status) === text;
}

function bodyText(entry, limit) {
  if (!entry.bodyChunks) {
    return entry.bodyDropped ? "(body dropped to save memory)" : null;
  }
  if (entry.contentType && !TEXT_TYPES.test(entry.contentType)) {
    return `(${entry.contentType} body, ${entry.responseSize} bytes, not shown)`;
  }
  const joined = new Uint8Array(entry.bodyStored);
  let at = 0;
  for (const c of entry.bodyChunks) {
    joined.set(new Uint8Array(c), at);
    at += c.byteLength;
  }
  let text = new TextDecoder("utf-8", { fatal: false }).decode(joined);
  if (text.length > limit) {
    text = text.slice(0, limit) + "\u2026";
  }
  return text + (entry.truncated ? `\n(truncated: ${entry.responseSize} bytes in all)` : "");
}

async function getNetworkCommand(p) {
  await getTab(p.tab);
  const state = captures.get(p.tab);
  if (!state || !state.network) {
    throw new CommandError("capture_off", "Network capture isn't on for this tab. Call set_capture with network: true first.");
  }
  const since = p.since_seq || 0;
  const wantMethod = p.method ? p.method.toUpperCase() : null;
  const wantUrl = p.url ? p.url.toLowerCase() : null;
  const matching = state.requests.filter(
    e =>
      e.seq > since &&
      (!wantMethod || e.method === wantMethod) &&
      (!wantUrl || e.url.toLowerCase().includes(wantUrl)) &&
      statusMatches(e.status, p.status)
  );
  const limit = p.limit || 50;
  const shown = matching.slice(-limit);
  const perBody = Math.max(500, Math.floor(40000 / Math.max(1, shown.length)));
  const requests = shown.map(e => {
    const out = {
      seq: e.seq,
      method: e.method,
      url: e.url,
      type: e.type,
      status: e.status ?? null,
      error: e.error || null,
      contentType: e.contentType || null,
      duration: e.duration ?? null,
      pending: state.pending.has(e.requestId),
    };
    if (p.bodies) {
      out.requestBody = e.requestBody;
      out.responseBody = bodyText(e, perBody);
    }
    return out;
  });
  return { requests, total: matching.length, lastSeq: state.requestSeq, tab: await tabInfo(p.tab), effects: [] };
}

async function getConsoleCommand(p) {
  await getTab(p.tab);
  const state = captures.get(p.tab);
  if (!state || !state.console) {
    throw new CommandError("capture_off", "Console capture isn't on for this tab. Call set_capture with console: true first.");
  }
  const min = LEVELS[p.level] ?? 0;
  const since = p.since_seq || 0;
  const matching = state.messages.filter(m => m.seq > since && (LEVELS[m.level] ?? 1) >= min);
  const shown = matching.slice(-(p.limit || 100));
  return { messages: shown, total: matching.length, lastSeq: state.messageSeq, tab: await tabInfo(p.tab), effects: [] };
}

function onConsoleMessage(message, sender) {
  const tabId = sender.tab && sender.tab.id;
  const state = tabId !== undefined && captures.get(tabId);
  if (!state || !state.console) {
    return;
  }
  for (const entry of message.entries || []) {
    keep(state.messages, { seq: ++state.messageSeq, frame: sender.frameId, ...entry });
  }
}

function watchCaptures() {
  browser.tabs.onRemoved.addListener(tabId => {
    captures.delete(tabId);
    updateNetworkListeners();
  });
}
