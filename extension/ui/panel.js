/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const COMMAND_NAMES = {
  open_tab: "Opened a tab",
  close_tabs: "Closed tabs",
  open_window: "Opened a window",
  close_window: "Closed a window",
  focus_tab: "Switched to a tab",
  unload_tab: "Unloaded a tab",
  navigate: "Navigated",
  snapshot: "Read the page outline",
  find: "Searched the page",
  read_page: "Read the page",
  get_html: "Read HTML",
  find_in_page: "Found in page",
  screenshot: "Took a screenshot",
  eval_page: "Ran a script",
  wait_for: "Waited",
  click: "Clicked",
  hover: "Hovered",
  type: "Typed",
  press_key: "Pressed keys",
  select_option: "Chose an option",
  scroll: "Scrolled",
  drag: "Dragged",
  upload_file: "Uploaded a file",
  set_dialog_policy: "Set dialog answers",
  set_capture: "Changed capture",
  get_network: "Read network requests",
  get_console: "Read the console",
  list_tabs: "Listed tabs",
  set_label: "Renamed this browser",
  search_history: "Searched history",
  search_bookmarks: "Searched bookmarks",
  add_bookmark: "Added a bookmark",
  list_downloads: "Listed downloads",
  recently_closed: "Listed closed tabs",
  restore_closed: "Restored a closed tab",
};

function timeText(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

async function render() {
  const state = await browser.runtime.sendMessage({ type: "panel_state" });
  const status = document.getElementById("status");
  if (state.paused) {
    status.textContent = `Paused. Agents can't use this Firefox (${state.label}) until you resume.`;
    status.className = "status paused";
  } else if (state.connected) {
    status.textContent = `Connected as ${state.label}.`;
    status.className = "status ok";
  } else if (state.helperRunning) {
    status.textContent = `Ready as ${state.label}; no agent has used it recently.`;
    status.className = "status ok";
  } else {
    status.textContent = "Not connected. The Agent F helper isn't running; check the installation.";
    status.className = "status bad";
  }
  const pause = document.getElementById("pause");
  pause.textContent = state.paused ? "Resume Agent F" : "Pause Agent F";
  pause.onclick = async () => {
    await browser.runtime.sendMessage({ type: "set_paused", paused: !state.paused });
    render();
  };
  const list = document.getElementById("recent");
  list.textContent = "";
  for (const a of state.recent) {
    const li = document.createElement("li");
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = timeText(a.time);
    const what = document.createElement("span");
    what.className = "what";
    what.textContent = COMMAND_NAMES[a.command] || a.command;
    const where = document.createElement("span");
    where.className = "where";
    where.textContent = a.title || a.url || (a.tab !== null ? `tab ${a.tab}` : "");
    li.append(time, what, where);
    if (a.error) {
      li.classList.add("failed");
      li.title = a.error;
    }
    list.append(li);
  }
  document.getElementById("empty").hidden = state.recent.length > 0;
}

document.getElementById("settings").addEventListener("click", e => {
  e.preventDefault();
  browser.runtime.openOptionsPage();
  window.close();
});

render();
