/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

// History, bookmarks, downloads and recently closed tabs.

// Accepts "24h", "7d", "2w", or anything Date.parse understands.
function parseSince(since) {
  if (!since) {
    return 0;
  }
  const m = /^(\d+)\s*([hdw])$/i.exec(String(since).trim());
  if (m) {
    const unit = { h: 3600e3, d: 86400e3, w: 7 * 86400e3 }[m[2].toLowerCase()];
    return Date.now() - Number(m[1]) * unit;
  }
  const t = Date.parse(since);
  if (Number.isNaN(t)) {
    throw new CommandError("bad_since", `Can't read ${since} as a time; use something like 24h, 7d or 2026-09-01.`);
  }
  return t;
}

async function searchHistoryCommand(p) {
  const items = await browser.history.search({
    text: p.text || "",
    startTime: parseSince(p.since),
    maxResults: Math.min(p.limit || 25, 200),
  });
  return {
    items: items.map(i => ({ title: i.title, url: i.url, lastVisit: i.lastVisitTime, visits: i.visitCount })),
  };
}

const folderTitles = new Map();

async function folderPath(parentId) {
  const parts = [];
  let id = parentId;
  for (let depth = 0; id && depth < 10; depth++) {
    let node = folderTitles.get(id);
    if (!node) {
      try {
        [node] = await browser.bookmarks.get(id);
      } catch (e) {
        break;
      }
      folderTitles.set(id, node);
    }
    if (node.title) {
      parts.unshift(node.title);
    }
    id = node.parentId;
  }
  return parts.join(" / ");
}

async function searchBookmarksCommand(p) {
  const found = (await browser.bookmarks.search(p.text || {})).filter(b => b.url).slice(0, Math.min(p.limit || 25, 200));
  const items = [];
  for (const b of found) {
    items.push({ title: b.title, url: b.url, folder: await folderPath(b.parentId), added: b.dateAdded });
  }
  return { items };
}

async function addBookmarkCommand(p) {
  let parentId;
  if (p.folder) {
    const folders = (await browser.bookmarks.search({ title: p.folder })).filter(b => !b.url);
    if (!folders.length) {
      throw new CommandError("no_such_folder", `There is no bookmark folder named ${p.folder}.`);
    }
    parentId = folders[0].id;
  }
  const bookmark = await browser.bookmarks.create({ url: p.url, title: p.title || p.url, parentId });
  return { title: bookmark.title, url: bookmark.url, folder: await folderPath(bookmark.parentId) };
}

async function listDownloadsCommand(p) {
  const query = { orderBy: ["-startTime"], limit: Math.min(p.limit || 20, 200) };
  if (p.state) {
    query.state = p.state;
  }
  if (p.since) {
    query.startedAfter = new Date(parseSince(p.since)).toISOString();
  }
  const items = await browser.downloads.search(query);
  return {
    items: items.map(d => ({
      filename: d.filename,
      url: d.url,
      state: d.state,
      error: d.error || null,
      bytes: d.bytesReceived,
      total: d.totalBytes,
      started: d.startTime,
      exists: d.exists,
    })),
  };
}

async function recentlyClosedCommand(p) {
  const sessions = await browser.sessions.getRecentlyClosed({ maxResults: Math.min(p.limit || 10, 25) });
  return {
    items: sessions.map(s =>
      s.tab
        ? { kind: "tab", sessionId: s.tab.sessionId, title: s.tab.title, url: s.tab.url, closed: s.lastModified }
        : {
            kind: "window",
            sessionId: s.window.sessionId,
            tabs: (s.window.tabs || []).length,
            title: s.window.tabs && s.window.tabs[0] ? s.window.tabs[0].title : "",
            closed: s.lastModified,
          }
    ),
  };
}

async function restoreClosedCommand(p) {
  let restored;
  try {
    restored = await browser.sessions.restore(p.session_id);
  } catch (e) {
    throw new CommandError("restore_failed", `Couldn't restore ${p.session_id}: ${e.message}`);
  }
  const tab = restored.tab || (restored.window && restored.window.tabs && restored.window.tabs[0]);
  const effects = ["Firefox restores closed tabs in the foreground; it can't restore them in the background."];
  if (tab) {
    touchTab(tab.id);
    return { kind: restored.tab ? "tab" : "window", tab: await tabInfo(tab.id), effects };
  }
  return { kind: restored.tab ? "tab" : "window", effects };
}
