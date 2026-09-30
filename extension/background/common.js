/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const AGENT_GROUP = "Agent F";
const AGENT_GROUP_COLOR = "orange";
const AGENT_TAB_TTL = 10 * 60 * 1000;

class CommandError extends Error {
  constructor(code, message, data) {
    super(message);
    this.code = code;
    this.data = data;
  }
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// Tabs an agent used recently; the user's own input in them is reported as an event.
const agentTabs = new Map();

function touchTab(tabId) {
  agentTabs.set(tabId, Date.now());
}

function isAgentTab(tabId) {
  const t = agentTabs.get(tabId);
  return t !== undefined && Date.now() - t < AGENT_TAB_TTL;
}

function summarizeTab(tab) {
  const summary = {
    id: tab.id,
    windowId: tab.windowId,
    index: tab.index,
    title: tab.title,
    url: tab.url,
    active: tab.active,
    pinned: tab.pinned,
    discarded: tab.discarded,
    status: tab.status,
    audible: tab.audible,
    incognito: tab.incognito,
  };
  if (tab.groupId !== undefined && tab.groupId !== -1) {
    summary.groupId = tab.groupId;
  }
  if (tab.cookieStoreId && tab.cookieStoreId !== "firefox-default" && !tab.incognito) {
    summary.cookieStoreId = tab.cookieStoreId;
  }
  if (tab.openerTabId !== undefined) {
    summary.openerTabId = tab.openerTabId;
  }
  return summary;
}

async function lastFocusedWindow() {
  try {
    return await browser.windows.getLastFocused({ windowTypes: ["normal"] });
  } catch (e) {
    return null;
  }
}

async function getTab(tabId) {
  try {
    return await browser.tabs.get(tabId);
  } catch (e) {
    throw new CommandError("no_such_tab", `Tab ${tabId} doesn't exist; it may have been closed.`);
  }
}

// A tab summary plus whether it's the tab the user has in front of them.
async function tabInfo(tabId) {
  const tab = await getTab(tabId);
  const summary = summarizeTab(tab);
  const focused = await lastFocusedWindow();
  summary.userTab = !!(tab.active && focused && focused.id === tab.windowId);
  return summary;
}
