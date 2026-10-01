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

/** The status pill's kind and headline, and the detail line as [before, browser name, after]. */
function describe(state) {
  const name = state.label;
  if (state.paused) {
    return ["bad", "Paused", ["Agents can't use this Firefox (", name, ") until you resume."]];
  }
  if (!state.helperRunning) {
    return ["bad", "Not connected", ["The Agent F helper isn't running. Check the installation.", "", ""]];
  }
  if (state.active) {
    return ["working", "Agent working", ["An agent is working in this Firefox, as ", name, "."]];
  }
  if (state.connected) {
    return ["ok", "Connected", ["Agents see this Firefox as ", name, "."]];
  }
  return ["ok", "Ready", ["Agents see this Firefox as ", name, ". None has used it recently."]];
}

async function render() {
  const state = await browser.runtime.sendMessage({ type: "panel_state" });
  const [kind, headline, detail] = describe(state);
  document.getElementById("status").className = `status ${kind}`;
  document.getElementById("status-text").textContent = headline;
  const strong = document.createElement("strong");
  strong.textContent = detail[1];
  document.getElementById("detail").replaceChildren(detail[0], ...(detail[1] ? [strong] : []), detail[2]);
  document.getElementById("franklin").classList.toggle("active", !!state.active && !state.paused);
  document.getElementById("version").textContent = `Version ${state.version}`;
  const pause = document.getElementById("pause");
  pause.textContent = state.paused ? "Resume Agent F" : "Pause Agent F";
  pause.classList.toggle("primary", state.paused);
  pause.onclick = async () => {
    await browser.runtime.sendMessage({ type: "set_paused", paused: !state.paused });
    render();
  };
  const list = document.getElementById("recent");
  const scrolled = list.scrollTop;
  list.textContent = "";
  for (const a of state.recent) {
    const li = document.createElement("li");
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = timeText(a.time);
    const what = document.createElement("span");
    what.className = "what";
    what.textContent = COMMAND_NAMES[a.command] || a.command;
    li.append(what, time);
    const whereText = a.title || a.url || (a.tab !== null ? `tab ${a.tab}` : "");
    if (whereText) {
      const where = document.createElement("span");
      where.className = "where";
      where.textContent = whereText;
      li.append(where);
    }
    if (a.error) {
      li.classList.add("failed");
      li.title = a.error;
    }
    list.append(li);
  }
  list.scrollTop = scrolled;
  document.getElementById("empty").hidden = state.recent.length > 0;
}

document.getElementById("settings").addEventListener("click", () => {
  browser.runtime.openOptionsPage();
  window.close();
});

document.getElementById("github").addEventListener("click", () => {
  browser.tabs.create({ url: "https://github.com/drubino-mozilla/agent-f" });
  window.close();
});

render();
setInterval(render, 2000);
