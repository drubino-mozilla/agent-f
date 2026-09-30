/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

var AgentF = typeof AgentF !== "undefined" ? AgentF : {};

{
  (function (A) {
    // Rebuilt on every load, so a re-injected (newer) copy takes over the commands.
    A.handlers = {
      ping: () => ({ doc: A.DOC_ID, url: location.href }),
      snapshot: A.snapshot,
      find: A.find,
      marks: A.marks,
      geometry: A.geometry,
      click: A.click,
      hover: A.hover,
      type: A.type,
      press_key: A.pressKey,
      select_option: A.selectOption,
      scroll: A.scroll,
      settle: A.settle,
      read_page: A.readPage,
      get_html: A.getHtml,
      check: A.check,
      text_context: A.textContext,
      upload_file: A.uploadFiles,
      drag: A.drag,
      set_dialog_policy: A.setDialogPolicy,
      enable_console: A.enableConsole,
    };

    if (!A.listening) {
      A.listening = true;
      browser.runtime.onMessage.addListener(message => {
        if (!message || message.agentF !== true) {
          return undefined;
        }
        return (async () => {
          try {
            // Commands carry the background page's build; an older copy of these files asks to be replaced.
            if (message.build && message.build !== A.build) {
              throw new A.AgentError("stale_content", "This page has an older copy of Agent F's page script.");
            }
            const handler = A.handlers[message.command];
            if (!handler) {
              throw new A.AgentError("unknown_command", `Unknown content command ${message.command}`);
            }
            return { ok: true, build: A.build, result: await handler(message.params || {}) };
          } catch (e) {
            return { ok: false, error: { code: e.code || "error", message: String(e.message || e), data: e.data } };
          }
        })();
      });
    }
  })(AgentF);
}
