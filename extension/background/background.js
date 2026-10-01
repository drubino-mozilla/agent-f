/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const NATIVE_HOST = "agent_f";
const PROTOCOL = 1;
const MAX_RETRY_DELAY = 30000;

let port = null;
let retryDelay = 1000;
let identity = null;
let paused = false;
let brokerSeen = 0;
let dialogDefaults = null;

// The toolbar panel's list of recent Agent F actions; each one also makes the toolbar fedora hop.
const recentActions = [];
const QUIET_COMMANDS = new Set(["ping", "resolve_tab", "upload_chunk", "reload_extension"]);

function recordAction(command, params, result, error) {
  if (QUIET_COMMANDS.has(command)) {
    return;
  }
  const tab = result && result.tab;
  recentActions.push({
    time: Date.now(),
    command,
    tab: tab ? tab.id : params.tab ?? null,
    title: tab ? tab.title : "",
    url: tab ? tab.url : "",
    error: error ? error.message : null,
  });
  if (recentActions.length > 30) {
    recentActions.splice(0, recentActions.length - 30);
  }
  hop();
}

async function setPaused(value) {
  paused = !!value;
  await browser.storage.local.set({ paused });
  browser.browserAction.setTitle({ title: paused ? "Agent F (paused)" : "Agent F" });
  await sendHello();
}

async function loadIdentity() {
  const stored = await browser.storage.local.get(["profileId", "label"]);
  let { profileId, label } = stored;
  if (!profileId) {
    profileId = crypto.randomUUID();
  }
  if (!label) {
    const info = await browser.runtime.getBrowserInfo();
    label = `${channelOf(info.version)}-${profileId.slice(0, 4)}`;
  }
  await browser.storage.local.set({ profileId, label });
  return { profileId, label };
}

function channelOf(version) {
  if (/a\d/.test(version)) {
    return "nightly";
  }
  if (/b\d/.test(version)) {
    return "beta";
  }
  return "release";
}

function post(message) {
  if (!port) {
    return;
  }
  try {
    port.postMessage(message);
  } catch (e) {
    console.warn("Agent F: could not send", e);
  }
}

async function sendHello() {
  const info = await browser.runtime.getBrowserInfo();
  post({
    type: "hello",
    protocol: PROTOCOL,
    profileId: identity.profileId,
    label: identity.label,
    browser: `${info.name} ${info.version}`,
    buildId: info.buildID,
    extensionVersion: browser.runtime.getManifest().version,
    paused,
  });
}

function connect() {
  port = browser.runtime.connectNative(NATIVE_HOST);
  port.onMessage.addListener(onMessage);
  port.onDisconnect.addListener(p => {
    const reason = p.error ? p.error.message : "closed";
    console.warn(`Agent F: helper disconnected (${reason}); retrying in ${retryDelay} ms`);
    port = null;
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY);
  });
  sendHello();
}

async function onMessage(message) {
  retryDelay = 1000;
  brokerSeen = Date.now();
  if (message.type !== "request") {
    return;
  }
  const handler = COMMANDS[message.command];
  const params = message.params || {};
  try {
    if (paused && message.command !== "ping") {
      throw new CommandError("paused", "The user paused Agent F in this Firefox (toolbar button). Ask them to resume it.");
    }
    if (!handler) {
      throw new CommandError("unknown_command", `Unknown command ${message.command}`);
    }
    const result = await handler(params);
    recordAction(message.command, params, result, null);
    post({ id: message.id, type: "response", result: result ?? null });
  } catch (e) {
    if (e.code !== "paused") {
      recordAction(message.command, params, null, e);
    }
    post({
      id: message.id,
      type: "response",
      error: { code: e.code || "error", message: String(e.message || e), data: e.data },
    });
  }
}

function matchesQuery(tab, query) {
  if (!query) {
    return true;
  }
  const q = query.toLowerCase();
  return (tab.title || "").toLowerCase().includes(q) || (tab.url || "").toLowerCase().includes(q);
}

async function listTabs({ window: windowId, query }) {
  let windows = await browser.windows.getAll({ populate: true, windowTypes: ["normal"] });
  if (windowId !== undefined && windowId !== null) {
    windows = windows.filter(w => w.id === windowId);
    if (!windows.length) {
      throw new CommandError("no_such_window", `There is no window ${windowId}.`);
    }
  }
  const lastFocused = await lastFocusedWindow();
  let groups = [];
  if (browser.tabGroups) {
    groups = (await browser.tabGroups.query({})).map(g => ({
      id: g.id,
      title: g.title,
      color: g.color,
      collapsed: g.collapsed,
      windowId: g.windowId,
    }));
  }
  let containers = [];
  try {
    containers = (await browser.contextualIdentities.query({})).map(c => ({
      cookieStoreId: c.cookieStoreId,
      name: c.name,
    }));
  } catch (e) {}
  return {
    focusedWindow: lastFocused ? lastFocused.id : null,
    firefoxFocused: lastFocused ? lastFocused.focused : false,
    windows: windows.map(w => ({
      id: w.id,
      focused: w.focused,
      incognito: w.incognito,
      state: w.state,
      tabs: w.tabs.filter(t => matchesQuery(t, query)).map(summarizeTab),
    })),
    groups,
    containers,
  };
}

async function setLabel({ label }) {
  if (typeof label !== "string" || !label) {
    throw new CommandError("bad_label", "A label is required.");
  }
  identity.label = label;
  await browser.storage.local.set({ label });
  await sendHello();
  return { label };
}

const action = command => p => runAction(p, command, p);

// Reloads the add-on from disk, after the reply goes out. For development: a linked install
// then picks up code changes without restarting Firefox.
async function reloadExtension() {
  setTimeout(() => browser.runtime.reload(), 200);
  return { reloading: true };
}

const COMMANDS = {
  ping: async () => ({ pong: true }),
  reload_extension: reloadExtension,
  list_tabs: listTabs,
  set_label: setLabel,
  resolve_tab: resolveTab,
  open_tab: openTabCommand,
  close_tabs: closeTabsCommand,
  open_window: openWindowCommand,
  close_window: closeWindowCommand,
  focus_tab: focusTabCommand,
  unload_tab: unloadTabCommand,
  navigate: navigateCommand,
  snapshot: snapshotCommand,
  find: findCommand,
  read_page: p => pageCommand(p, "read_page"),
  get_html: p => pageCommand(p, "get_html"),
  find_in_page: findInPageCommand,
  screenshot: screenshotCommand,
  eval_page: evalPageCommand,
  wait_for: waitForCommand,
  click: action("click"),
  hover: action("hover"),
  type: action("type"),
  press_key: action("press_key"),
  select_option: action("select_option"),
  scroll: action("scroll"),
  drag: action("drag"),
  upload_chunk: uploadChunkCommand,
  upload_file: uploadFileCommand,
  set_dialog_policy: setDialogPolicyCommand,
  set_capture: setCaptureCommand,
  get_network: getNetworkCommand,
  get_console: getConsoleCommand,
  search_history: searchHistoryCommand,
  search_bookmarks: searchBookmarksCommand,
  add_bookmark: addBookmarkCommand,
  list_downloads: listDownloadsCommand,
  recently_closed: recentlyClosedCommand,
  restore_closed: restoreClosedCommand,
};

// Sets the dialog answers in every frame of the tab, so dialogs from iframes follow them too.
async function setDialogPolicyCommand(p) {
  const effects = [];
  await readyTab(p.tab, effects);
  let result = null;
  for (const frame of await allFrames(p.tab)) {
    try {
      const r = await sendToFrame(p.tab, frame.frameId, "set_dialog_policy", p);
      if (frame.frameId === 0) {
        result = r;
      }
    } catch (e) {
      if (frame.frameId === 0) {
        throw e;
      }
    }
  }
  return { ...result, tab: await tabInfo(p.tab), effects };
}

// Messages from content scripts, the toolbar panel and the options page.
function onRuntimeMessage(message, sender) {
  if (!message || typeof message.type !== "string") {
    return undefined;
  }
  switch (message.type) {
    case "user_input":
      if (sender.tab && isAgentTab(sender.tab.id)) {
        reportEvent("user_input", { tab: sender.tab.id, window: sender.tab.windowId, data: { kind: message.kind } });
      }
      return undefined;
    case "console":
      onConsoleMessage(message, sender);
      return undefined;
    case "capture_state": {
      const state = sender.tab ? captures.get(sender.tab.id) : null;
      return Promise.resolve({ console: !!(state && state.console), dialogPolicy: dialogDefaults });
    }
    case "panel_state":
      return Promise.resolve({
        label: identity.label,
        paused,
        connected: !!port && Date.now() - brokerSeen < 5 * 60 * 1000,
        helperRunning: !!port,
        recent: recentActions.slice(-20).reverse(),
      });
    case "set_paused":
      return setPaused(message.paused).then(() => ({ paused }));
    case "set_label":
      return setLabel({ label: message.label });
    case "get_options":
      return Promise.resolve({ label: identity.label, dialogPolicy: dialogDefaults });
    case "set_dialog_defaults":
      dialogDefaults = message.dialogPolicy;
      return browser.storage.local.set({ dialogPolicy: dialogDefaults }).then(() => ({ dialogPolicy: dialogDefaults }));
    default:
      return undefined;
  }
}

const UPDATE_KEYS = ["url", "title", "status", "discarded", "pinned", "groupId", "audible"];

function reportEvent(event, fields) {
  post({ type: "event", event, ...fields });
}

function watchBrowser() {
  browser.tabs.onCreated.addListener(tab => {
    reportEvent("tab_created", { tab: tab.id, window: tab.windowId, info: summarizeTab(tab) });
  });
  browser.tabs.onRemoved.addListener((tabId, removeInfo) => {
    agentTabs.delete(tabId);
    reportEvent("tab_removed", {
      tab: tabId,
      window: removeInfo.windowId,
      data: { isWindowClosing: removeInfo.isWindowClosing },
    });
  });
  browser.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    const changed = UPDATE_KEYS.filter(k => k in changeInfo);
    if (!changed.length) {
      return;
    }
    reportEvent("tab_updated", {
      tab: tabId,
      window: tab.windowId,
      info: summarizeTab(tab),
      data: { changed },
    });
  });
  browser.tabs.onActivated.addListener(({ tabId, previousTabId, windowId }) => {
    reportEvent("tab_activated", { tab: tabId, window: windowId, data: { previousTabId } });
  });
  browser.tabs.onAttached.addListener((tabId, { newWindowId }) => {
    reportEvent("tab_attached", { tab: tabId, window: newWindowId, info: { id: tabId, windowId: newWindowId } });
  });
  browser.windows.onCreated.addListener(win => {
    if (win.type === "normal") {
      reportEvent("window_created", { window: win.id });
    }
  });
  browser.windows.onRemoved.addListener(windowId => {
    reportEvent("window_removed", { window: windowId });
  });
  browser.windows.onFocusChanged.addListener(windowId => {
    reportEvent("window_focused", { window: windowId });
  });
  if (browser.tabGroups) {
    browser.tabGroups.onCreated.addListener(g => {
      reportEvent("group_created", { window: g.windowId, data: { id: g.id, title: g.title } });
    });
    browser.tabGroups.onRemoved.addListener(g => {
      reportEvent("group_removed", { window: g.windowId, data: { id: g.id, title: g.title } });
    });
  }
  browser.runtime.onMessage.addListener(onRuntimeMessage);
}

(async function start() {
  identity = await loadIdentity();
  const stored = await browser.storage.local.get(["paused", "dialogPolicy"]);
  paused = !!stored.paused;
  dialogDefaults = stored.dialogPolicy || null;
  if (paused) {
    browser.browserAction.setTitle({ title: "Agent F (paused)" });
  }
  watchBrowser();
  watchCaptures();
  trackRequests();
  connect();
})();
