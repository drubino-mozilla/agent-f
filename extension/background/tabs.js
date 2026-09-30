/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

// Follows one tab's top-level navigation, so callers can wait for it to commit or finish.
function trackNavigation(tabId) {
  const state = { started: false, committed: false, complete: false, closed: false, error: null, url: null };
  const waiters = new Set();
  const notify = () => waiters.forEach(w => w());
  const mine = d => d.tabId === tabId && d.frameId === 0;
  const onBefore = d => {
    if (mine(d)) {
      state.started = true;
      state.complete = false;
      notify();
    }
  };
  const onCommitted = d => {
    if (mine(d)) {
      state.started = state.committed = true;
      state.url = d.url;
      notify();
    }
  };
  const onCompleted = d => {
    if (mine(d)) {
      state.complete = true;
      state.url = d.url;
      notify();
    }
  };
  const onError = d => {
    // NS_BINDING_ABORTED: this load was superseded (for example by a back-forward cache restore).
    if (mine(d) && !/2152398850|NS_BINDING_ABORTED/.test(String(d.error))) {
      state.complete = true;
      state.error = d.error;
      notify();
    }
  };
  const onRemoved = id => {
    if (id === tabId) {
      state.closed = true;
      notify();
    }
  };
  browser.webNavigation.onBeforeNavigate.addListener(onBefore);
  browser.webNavigation.onCommitted.addListener(onCommitted);
  browser.webNavigation.onCompleted.addListener(onCompleted);
  browser.webNavigation.onErrorOccurred.addListener(onError);
  browser.tabs.onRemoved.addListener(onRemoved);

  // Resolves true when cond holds, false on timeout, "not_started" if nothing began in time.
  state.wait = (cond, timeout, startTimeout = null) =>
    new Promise(resolve => {
      let timer = null;
      let startTimer = null;
      const done = value => {
        waiters.delete(check);
        clearTimeout(timer);
        clearTimeout(startTimer);
        resolve(value);
      };
      const check = () => {
        if (cond(state)) {
          done(true);
        }
      };
      waiters.add(check);
      timer = setTimeout(() => done(false), timeout);
      if (startTimeout !== null) {
        startTimer = setTimeout(() => {
          if (!state.started && !state.closed) {
            done("not_started");
          }
        }, startTimeout);
      }
      check();
    });

  state.stop = () => {
    browser.webNavigation.onBeforeNavigate.removeListener(onBefore);
    browser.webNavigation.onCommitted.removeListener(onCommitted);
    browser.webNavigation.onCompleted.removeListener(onCompleted);
    browser.webNavigation.onErrorOccurred.removeListener(onError);
    browser.tabs.onRemoved.removeListener(onRemoved);
    waiters.clear();
  };
  return state;
}

async function waitForTabComplete(tabId, timeout) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    let tab;
    try {
      tab = await browser.tabs.get(tabId);
    } catch (e) {
      return "closed";
    }
    if (tab.status === "complete" && Date.now() - start > 150) {
      return "complete";
    }
    await sleep(100);
  }
  return "timeout";
}

// Reloads a tab Firefox unloaded to save memory, so there is a page to talk to.
async function readyTab(tabId, effects) {
  const tab = await getTab(tabId);
  touchTab(tabId);
  if (tab.discarded) {
    await browser.tabs.reload(tabId);
    await waitForTabComplete(tabId, 20000);
    effects.push(`Tab ${tabId} had been unloaded (Firefox does this to save memory); Agent F reloaded it.`);
  }
  return tab;
}

async function addToGroup(tabId, windowId, title) {
  if (!browser.tabGroups) {
    return null;
  }
  const existing = await browser.tabGroups.query({ windowId, title });
  if (existing.length) {
    return browser.tabs.group({ tabIds: [tabId], groupId: existing[0].id });
  }
  const groupId = await browser.tabs.group({ tabIds: [tabId], createProperties: { windowId } });
  await browser.tabGroups.update(groupId, { title, color: AGENT_GROUP_COLOR });
  return groupId;
}

async function containerId(name) {
  const ids = await browser.contextualIdentities.query({ name });
  if (!ids.length) {
    const all = (await browser.contextualIdentities.query({})).map(c => c.name).join(", ");
    throw new CommandError("no_such_container", `There is no container named ${name}. Containers: ${all || "none"}.`);
  }
  return ids[0].cookieStoreId;
}

async function createTab({ url, windowId, group, container, background = true, openerTabId }, effects) {
  const targetWindow = windowId ?? (await lastFocusedWindow())?.id;
  const props = { url: url || "about:blank", active: !background };
  if (targetWindow !== undefined && targetWindow !== null) {
    props.windowId = targetWindow;
  }
  if (container) {
    props.cookieStoreId = await containerId(container);
  }
  if (openerTabId !== undefined) {
    props.openerTabId = openerTabId;
  }
  let tab;
  try {
    tab = await browser.tabs.create(props);
  } catch (e) {
    throw new CommandError("open_failed", `Firefox wouldn't open ${props.url}: ${e.message}`);
  }
  touchTab(tab.id);
  try {
    await browser.tabs.update(tab.id, { autoDiscardable: false });
  } catch (e) {}
  const groupTitle = group === undefined || group === null ? AGENT_GROUP : group;
  if (groupTitle) {
    try {
      await addToGroup(tab.id, tab.windowId, groupTitle);
    } catch (e) {
      effects.push(`Couldn't add it to the ${groupTitle} tab group: ${e.message}`);
    }
  }
  return tab;
}

// Watches for tabs opened while a load is in progress, so that if another extension (such as
// Taskbar Tabs) moves the page to a new tab or window, the agent can follow it.
function watchNewTabs() {
  const created = [];
  const onCreated = tab => created.push(tab.id);
  browser.tabs.onCreated.addListener(onCreated);
  return {
    stop: () => browser.tabs.onCreated.removeListener(onCreated),
    async find(url, excludeId) {
      let host;
      try {
        host = new URL(url).host;
      } catch (e) {
        return null;
      }
      for (let i = 0; i < 20; i++) {
        for (const id of [...created].reverse()) {
          if (id === excludeId) {
            continue;
          }
          try {
            const tab = await browser.tabs.get(id);
            const tabUrl = tab.url === "about:blank" && tab.pendingUrl ? tab.pendingUrl : tab.url;
            if (new URL(tabUrl).host === host) {
              return tab;
            }
          } catch (e) {}
        }
        await sleep(150);
      }
      return null;
    },
  };
}

async function relocatedEffect(tab, effects) {
  touchTab(tab.id);
  await waitForTabComplete(tab.id, 15000);
  effects.push(
    `Another extension (Taskbar Tabs, for example) moved the page to tab ${tab.id} in window ${tab.windowId}. That tab is now your current tab.`
  );
  return tabInfo(tab.id);
}

async function resolveTab() {
  const focused = await lastFocusedWindow();
  if (!focused) {
    throw new CommandError("no_window", "Firefox has no open window.");
  }
  const [tab] = await browser.tabs.query({ windowId: focused.id, active: true });
  if (!tab) {
    throw new CommandError("no_tab", "The focused window has no active tab.");
  }
  return { tab: await tabInfo(tab.id) };
}

async function openTabCommand(p) {
  const effects = [];
  const watcher = watchNewTabs();
  let tab;
  try {
    tab = await createTab(
      { url: p.url, windowId: p.window, group: p.group, container: p.container, background: p.background !== false },
      effects
    );
    if (p.url && p.url !== "about:blank") {
      const status = await waitForTabComplete(tab.id, (p.timeout || 20) * 1000);
      if (status === "timeout") {
        effects.push("The page was still loading when Agent F stopped waiting.");
      } else if (status === "closed") {
        const moved = await watcher.find(p.url, tab.id);
        if (moved) {
          const kept = effects.filter(e => !e.startsWith("Couldn't add it to"));
          const info = await relocatedEffect(moved, kept);
          return { tab: info, effects: kept };
        }
        throw new CommandError("tab_closed", `Tab ${tab.id} closed while loading ${p.url}, and Agent F couldn't find where the page went.`);
      }
    }
  } finally {
    watcher.stop();
  }
  const info = await tabInfo(tab.id);
  if (p.url && info.url !== p.url && info.url !== "about:blank") {
    effects.push(`It ended up at ${info.url}.`);
  }
  return { tab: info, effects };
}

async function closeTabsCommand(p) {
  const ids = p.tabs && p.tabs.length ? p.tabs : [p.tab];
  const closed = [];
  const missing = [];
  for (const id of ids) {
    try {
      const tab = await browser.tabs.get(id);
      closed.push(summarizeTab(tab));
    } catch (e) {
      missing.push(id);
    }
  }
  if (closed.length) {
    await browser.tabs.remove(closed.map(t => t.id));
  }
  return { closed, missing };
}

async function openWindowCommand(p) {
  let win;
  try {
    win = await browser.windows.create({ url: p.url || undefined, incognito: !!p.private });
  } catch (e) {
    const hint = p.private ? " Private windows need the add-on to be allowed in private windows (about:addons)." : "";
    throw new CommandError("open_failed", `Firefox wouldn't open a window: ${e.message}.${hint}`);
  }
  const tab = win.tabs && win.tabs[0];
  if (tab) {
    touchTab(tab.id);
    if (p.url) {
      await waitForTabComplete(tab.id, 20000);
    }
  }
  return {
    window: win.id,
    tab: tab ? await tabInfo(tab.id) : null,
    effects: ["Firefox brought the new window to the front; it can't open windows in the background."],
  };
}

async function closeWindowCommand(p) {
  let win;
  try {
    win = await browser.windows.get(p.window, { populate: true });
  } catch (e) {
    throw new CommandError("no_such_window", `There is no window ${p.window}.`);
  }
  await browser.windows.remove(p.window);
  return { window: p.window, tabs: (win.tabs || []).length };
}

async function unloadTabCommand(p) {
  const tab = await getTab(p.tab);
  if (tab.active) {
    throw new CommandError("tab_active", "Firefox can't unload the tab that's showing in its window.");
  }
  await browser.tabs.discard(p.tab);
  return { tab: summarizeTab(await getTab(p.tab)), effects: [] };
}

async function focusTabCommand(p) {
  const tab = await getTab(p.tab);
  await browser.tabs.update(p.tab, { active: true });
  await browser.windows.update(tab.windowId, { focused: true });
  return { tab: await tabInfo(p.tab), effects: [] };
}

async function navigateCommand(p) {
  const effects = [];
  const before = await getTab(p.tab);
  touchTab(p.tab);
  const nav = trackNavigation(p.tab);
  const watcher = watchNewTabs();
  const timeout = (p.timeout || 20) * 1000;
  const until = p.wait || "load";
  try {
    if (p.url) {
      try {
        await browser.tabs.update(p.tab, { url: p.url });
      } catch (e) {
        throw new CommandError("open_failed", `Firefox wouldn't load ${p.url}: ${e.message}`);
      }
    } else if (p.action === "back") {
      await browser.tabs.goBack(p.tab);
    } else if (p.action === "forward") {
      await browser.tabs.goForward(p.tab);
    } else if (p.action === "reload") {
      await browser.tabs.reload(p.tab);
    } else {
      throw new CommandError("bad_arguments", "Pass url, or action: back, forward or reload.");
    }
    if (until !== "none") {
      const cond = s => s.closed || (until === "commit" ? s.committed : s.complete);
      const outcome = await nav.wait(cond, timeout, p.action === "back" || p.action === "forward" ? 2000 : null);
      if (outcome === "not_started") {
        effects.push(`There was nothing to go ${p.action} to, or the page handled it without loading.`);
      } else if (outcome === false) {
        effects.push(`The page was still loading after ${timeout / 1000} s.`);
      }
    }
    if (nav.closed) {
      const moved = p.url ? await watcher.find(p.url, p.tab) : null;
      if (moved) {
        return { tab: await relocatedEffect(moved, effects), effects };
      }
      throw new CommandError("tab_closed", `Tab ${p.tab} closed while loading, and Agent F couldn't find where the page went.`);
    }
    if (nav.error) {
      effects.push(`Loading failed: ${nav.error}.`);
    }
  } finally {
    nav.stop();
    watcher.stop();
  }
  if (until === "load") {
    await waitForTabComplete(p.tab, 3000);
  }
  const info = await tabInfo(p.tab);
  if (p.url && info.url !== p.url) {
    effects.push(`It ended up at ${info.url}.`);
  } else if (!p.url && info.url !== before.url) {
    effects.push(`Now at ${info.url}.`);
  }
  return { tab: info, effects };
}
