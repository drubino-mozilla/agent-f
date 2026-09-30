/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

function flash(id) {
  const el = document.getElementById(id);
  el.hidden = false;
  setTimeout(() => (el.hidden = true), 2000);
}

async function load() {
  const options = await browser.runtime.sendMessage({ type: "get_options" });
  document.getElementById("label").value = options.label;
  const policy = options.dialogPolicy || {};
  document.getElementById("confirm").value = policy.confirm === "dismiss" ? "dismiss" : "accept";
}

document.getElementById("label-form").addEventListener("submit", async e => {
  e.preventDefault();
  await browser.runtime.sendMessage({ type: "set_label", label: document.getElementById("label").value });
  flash("label-saved");
});

document.getElementById("dialog-form").addEventListener("submit", async e => {
  e.preventDefault();
  await browser.runtime.sendMessage({
    type: "set_dialog_defaults",
    dialogPolicy: { alert: "accept", confirm: document.getElementById("confirm").value, prompt: "dismiss" },
  });
  flash("dialog-saved");
});

load();
