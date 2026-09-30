/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

var AgentF = typeof AgentF !== "undefined" ? AgentF : {};

// Wrappers installed into the page's own world: dialogs, window.open and console. They behave
// exactly like the originals unless an agent action is running (dialogs, popups) or capture is on
// (console), so the user's own browsing is unaffected.
{
  (function (A) {
    const page = window.wrappedJSObject;

    A.dialogPolicy = A.dialogPolicy || { alert: "accept", confirm: "accept", prompt: "dismiss" };
    A.dialogOnce = A.dialogOnce || null;
    A.agentActiveUntil = A.agentActiveUntil || 0;
    A.pageEvents = A.pageEvents || [];

    A.agentActive = () => performance.now() < A.agentActiveUntil;
    A.markAgentActive = ms => {
      A.agentActiveUntil = Math.max(A.agentActiveUntil, performance.now() + ms);
    };
    A.takePageEvents = () => A.pageEvents.splice(0);

    A.setDialogPolicy = function (params) {
      const rule = {};
      for (const type of ["alert", "confirm", "prompt"]) {
        if (params[type] !== undefined && params[type] !== null) {
          rule[type] = params[type];
        }
      }
      if (params.once === false) {
        Object.assign(A.dialogPolicy, rule);
      } else {
        A.dialogOnce = Object.keys(rule).length ? rule : null;
      }
      return { policy: { ...A.dialogPolicy }, once: A.dialogOnce };
    };

    function answerFor(type) {
      if (A.dialogOnce && A.dialogOnce[type] !== undefined) {
        const value = A.dialogOnce[type];
        A.dialogOnce = null;
        return value;
      }
      return A.dialogPolicy[type];
    }

    if (page && !A.pageHooksInstalled) {
      A.pageHooksInstalled = true;
      const original = { alert: page.alert, confirm: page.confirm, prompt: page.prompt, open: page.open };

      // Arguments are passed on one by one: page functions can't read this script's own
      // arguments object, so .apply(page, arguments) would throw inside the page.
      const dialog = type =>
        exportFunction(function (...args) {
          const [message] = args;
          if (!A.agentActive()) {
            return original[type].call(page, ...args);
          }
          const text = A.quote(A.clean(String(message ?? ""), 200));
          const answer = answerFor(type);
          if (type === "alert") {
            A.pageEvents.push(`The page showed alert(${text}); Agent F dismissed it.`);
            return undefined;
          }
          if (type === "confirm") {
            const ok = answer !== "dismiss";
            A.pageEvents.push(`The page asked confirm(${text}); Agent F answered ${ok ? "OK" : "Cancel"} by policy.`);
            return ok;
          }
          const value = !answer || answer === "dismiss" ? null : String(answer);
          A.pageEvents.push(
            `The page asked prompt(${text}); Agent F answered ${value === null ? "Cancel" : A.quote(value)} by policy.`
          );
          return value;
        }, window);

      try {
        page.alert = dialog("alert");
        page.confirm = dialog("confirm");
        page.prompt = dialog("prompt");
      } catch (e) {}

      try {
        page.open = exportFunction(function (...args) {
          const [url] = args;
          const opened = original.open.call(page, ...args);
          if (!opened && A.agentActive()) {
            const where = url ? A.shortUrl(String(url)) : "a new window";
            A.pageEvents.push(
              `The page tried to open ${where} in a popup, and Firefox blocked it because the click was simulated. Use open_tab with that address if you need it.`
            );
          }
          return opened;
        }, window);
      } catch (e) {}
    }

    // Console capture, switched on per tab by set_capture. Entries go to the background page,
    // which keeps them across navigations.
    let queue = [];
    let flushTimer = null;
    const flush = () => {
      flushTimer = null;
      const entries = queue;
      queue = [];
      if (entries.length) {
        browser.runtime.sendMessage({ type: "console", entries }).catch(() => {});
      }
    };
    const describeArg = arg => {
      if (typeof arg === "string") {
        return arg;
      }
      if (arg === null || arg === undefined || typeof arg !== "object") {
        return String(arg);
      }
      try {
        if (typeof arg.message === "string" && typeof arg.stack === "string") {
          return `${arg.name || "Error"}: ${arg.message}`;
        }
        const json = page.JSON.stringify(arg);
        if (json !== undefined) {
          return json;
        }
      } catch (e) {}
      return String(arg);
    };
    A.recordConsole = function (level, args) {
      let text = "";
      try {
        text = Array.from(args, describeArg).join(" ");
      } catch (e) {
        text = "(unreadable message)";
      }
      queue.push({ level, text: text.slice(0, 2000), time: Date.now(), url: location.href });
      if (!flushTimer) {
        flushTimer = setTimeout(flush, 250);
      }
    };

    A.enableConsole = function () {
      if (A.consoleWrapped || !page) {
        return { wrapped: !!A.consoleWrapped };
      }
      A.consoleWrapped = true;
      for (const level of ["log", "info", "warn", "error", "debug"]) {
        const orig = page.console[level];
        try {
          page.console[level] = exportFunction(function (...args) {
            try {
              A.recordConsole(level, args);
            } catch (e) {}
            return orig.call(page.console, ...args);
          }, window);
        } catch (e) {}
      }
      window.addEventListener("error", e => {
        const where = e.filename ? ` (${A.shortUrl(e.filename)}:${e.lineno})` : "";
        A.recordConsole("error", [`Uncaught ${e.message}${where}`]);
      });
      window.addEventListener("unhandledrejection", e => {
        A.recordConsole("error", ["Unhandled promise rejection:", e.reason]);
      });
      return { wrapped: true };
    };

    if (!A.captureAsked) {
      A.captureAsked = true;
      browser.runtime
        .sendMessage({ type: "capture_state" })
        .then(state => {
          if (state && state.console) {
            A.enableConsole();
          }
          if (state && state.dialogPolicy) {
            Object.assign(A.dialogPolicy, state.dialogPolicy);
          }
        })
        .catch(() => {});
    }
  })(AgentF);
}
