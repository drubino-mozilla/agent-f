/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

var AgentF = typeof AgentF !== "undefined" ? AgentF : {};

{
  (function (A) {
    const { AgentError } = A;

    // Trusted events during and just after our own actions come from Firefox reacting to us
    // (execCommand, focus), not from the user. Kept on A so listeners from an earlier load see it.
    A.actingUntil = A.actingUntil || 0;
    const markActing = () => {
      A.actingUntil = performance.now() + 600;
      // Dialogs and popups from now until the action settles are answered and reported by Agent F.
      A.markAgentActive(5000);
    };

    if (!A.userInputWatched) {
      A.userInputWatched = true;
      const lastReported = {};
      const reportUserInput = kind => event => {
        if (!event.isTrusted || performance.now() < A.actingUntil) {
          return;
        }
        const now = performance.now();
        if (now - (lastReported[kind] || -Infinity) < 2000) {
          return;
        }
        lastReported[kind] = now;
        browser.runtime.sendMessage({ type: "user_input", kind }).catch(() => {});
      };
      window.addEventListener("pointerdown", reportUserInput("clicked"), { capture: true, passive: true });
      window.addEventListener("keydown", reportUserInput("typed"), { capture: true, passive: true });
    }

    // What an action caused on the page: modal regions that appeared and where focus went.
    let watch = null;
    let lastMutation = performance.now();
    const MODAL_SELECTOR = "[role=dialog], [role=alertdialog], dialog[open], [aria-modal=true]";

    function startWatch(target = null) {
      stopWatch();
      const w = { focusBefore: A.deepActiveElement(), target, modals: new Set(), observer: null };
      w.observer = new MutationObserver(mutations => {
        lastMutation = performance.now();
        for (const m of mutations) {
          const candidates = m.type === "attributes" ? [m.target] : Array.from(m.addedNodes);
          for (const n of candidates) {
            if (n.nodeType !== Node.ELEMENT_NODE) {
              continue;
            }
            const found = n.matches(MODAL_SELECTOR) ? [n] : Array.from(n.querySelectorAll(MODAL_SELECTOR));
            for (const f of found) {
              w.modals.add(f);
            }
          }
        }
      });
      w.observer.observe(document.documentElement, {
        childList: true, subtree: true, attributes: true, characterData: true,
      });
      watch = w;
      lastMutation = performance.now();
    }

    function stopWatch() {
      if (watch) {
        watch.observer.disconnect();
      }
      watch = null;
    }

    A.settle = async function ({ quiet_ms = 300, max_ms = 3000 }) {
      const ownWatch = !watch;
      if (ownWatch) {
        startWatch();
      }
      const start = performance.now();
      let settled = false;
      while (performance.now() - start < max_ms) {
        if (performance.now() - lastMutation >= quiet_ms && document.readyState !== "loading") {
          settled = true;
          break;
        }
        await A.sleep(50);
      }
      const effects = [];
      const w = watch;
      if (w) {
        for (const m of w.modals) {
          if (m.isConnected && !A.isHidden(m)) {
            effects.push(`A ${A.describe(m)} appeared.`);
          }
        }
        const focus = A.deepActiveElement();
        const onTarget = w.target && (focus === w.target || composedContains(w.target, focus));
        if (focus && focus !== w.focusBefore && !onTarget && focus !== document.body && focus !== document.documentElement) {
          effects.push(`Focus moved to ${A.describe(focus)}.`);
        }
      }
      stopWatch();
      effects.unshift(...A.takePageEvents());
      A.agentActiveUntil = performance.now() + 300;
      return { settled, effects };
    };

    // Low-level event dispatch.
    function mouseEvent(target, type, x, y, opts = {}) {
      const Ctor = type.startsWith("pointer") ? PointerEvent : MouseEvent;
      const noBubble = ["mouseenter", "mouseleave", "pointerenter", "pointerleave"].includes(type);
      const init = {
        bubbles: !noBubble,
        cancelable: !noBubble,
        composed: true,
        clientX: x,
        clientY: y,
        screenX: x + window.mozInnerScreenX,
        screenY: y + window.mozInnerScreenY,
        button: opts.button || 0,
        buttons: opts.buttons || 0,
        detail: opts.detail || 0,
        ctrlKey: !!opts.ctrl,
        shiftKey: !!opts.shift,
        altKey: !!opts.alt,
        metaKey: !!opts.meta,
        view: window,
      };
      if (Ctor === PointerEvent) {
        Object.assign(init, { pointerId: 1, pointerType: "mouse", isPrimary: true, width: 1, height: 1 });
      }
      return target.dispatchEvent(new Ctor(type, init));
    }

    function elementAt(x, y) {
      let el = document.elementFromPoint(x, y);
      while (el && el.openOrClosedShadowRoot) {
        const inner = el.openOrClosedShadowRoot.elementFromPoint(x, y);
        if (!inner || inner === el) {
          break;
        }
        el = inner;
      }
      return el;
    }

    function composedContains(outer, inner) {
      for (let n = inner; n; n = n.parentNode || (n instanceof ShadowRoot ? n.host : null)) {
        if (n === outer) {
          return true;
        }
      }
      return false;
    }

    function ensureInView(el) {
      if (A.isOffscreen(el)) {
        el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      }
    }

    function pointFor(el, params) {
      ensureInView(el);
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) {
        throw new AgentError("not_visible", `The ${A.describe(el)} has no size on the page.`);
      }
      const x = params.x !== undefined && params.x !== null ? r.left + params.x : r.left + r.width / 2;
      const y = params.y !== undefined && params.y !== null ? r.top + params.y : r.top + r.height / 2;
      return { x, y };
    }

    function hitTarget(el, x, y) {
      const hit = elementAt(x, y);
      if (!hit) {
        return el;
      }
      if (composedContains(el, hit)) {
        return hit;
      }
      if (composedContains(hit, el)) {
        return el;
      }
      if (hit.localName === "label" && hit.control === el) {
        return el;
      }
      throw new AgentError("obstructed", `Something else covers that point: ${A.describe(hit)}. Deal with it first, or click it.`);
    }

    function modifiersOf(list) {
      const m = {};
      for (const k of list || []) {
        const key = String(k).toLowerCase();
        if (key === "control" || key === "ctrl") {
          m.ctrl = true;
        } else if (key === "shift") {
          m.shift = true;
        } else if (key === "alt") {
          m.alt = true;
        } else if (key === "meta" || key === "cmd" || key === "command") {
          m.meta = true;
        }
      }
      return m;
    }

    A.click = async function (params) {
      markActing();
      const el = A.resolveTarget(params);
      const { x, y } = pointFor(el, params);
      const target = hitTarget(el, x, y);
      const mods = modifiersOf(params.modifiers);
      const button = { left: 0, middle: 1, right: 2 }[params.button || "left"] ?? 0;
      const buttons = [1, 4, 2][button];

      const link = target.closest ? target.closest("a[href]") : null;
      const wantsNewTab = link && (link.target === "_blank" || mods.ctrl || mods.meta || mods.shift || button === 1);
      if (wantsNewTab && !link.hasAttribute("download") && !link.href.startsWith("javascript:")) {
        // Firefox blocks new tabs opened by simulated clicks. Let the page's own handlers run, then
        // cancel the default navigation (unless the page did) and have the background open the tab.
        let pageHandled = false;
        const stopper = e => {
          if (e.defaultPrevented) {
            pageHandled = true;
          } else {
            e.preventDefault();
          }
        };
        window.addEventListener("click", stopper, { once: true });
        startWatch(el);
        mouseEvent(target, "click", x, y, { ...mods, detail: 1 });
        window.removeEventListener("click", stopper);
        return { openInNewTab: pageHandled ? null : link.href, target: A.describe(el) };
      }

      startWatch(el);
      mouseEvent(target, "pointerover", x, y, mods);
      mouseEvent(target, "pointerenter", x, y, mods);
      mouseEvent(target, "mouseover", x, y, mods);
      mouseEvent(target, "mouseenter", x, y, mods);
      mouseEvent(target, "pointermove", x, y, mods);
      mouseEvent(target, "mousemove", x, y, mods);
      const count = Math.max(1, Math.min(3, params.count || 1));
      for (let i = 1; i <= count; i++) {
        const downAllowed =
          mouseEvent(target, "pointerdown", x, y, { ...mods, button, buttons, detail: i }) &&
          mouseEvent(target, "mousedown", x, y, { ...mods, button, buttons, detail: i });
        if (downAllowed && typeof el.focus === "function" && i === 1) {
          const focusable = el.closest("a[href], button, input, select, textarea, [tabindex], [contenteditable]");
          if (focusable && typeof focusable.focus === "function") {
            focusable.focus({ preventScroll: true });
          }
        }
        mouseEvent(target, "pointerup", x, y, { ...mods, button, detail: i });
        mouseEvent(target, "mouseup", x, y, { ...mods, button, detail: i });
        if (button === 2) {
          mouseEvent(target, "contextmenu", x, y, { ...mods, button, buttons, detail: i });
        } else if (button === 0) {
          mouseEvent(target, "click", x, y, { ...mods, button, detail: i });
        } else {
          mouseEvent(target, "auxclick", x, y, { ...mods, button, detail: i });
        }
      }
      if (count === 2 && button === 0) {
        mouseEvent(target, "dblclick", x, y, { ...mods, detail: 2 });
      }
      return { target: A.describe(el) };
    };

    A.hover = async function (params) {
      markActing();
      const el = A.resolveTarget(params);
      const { x, y } = pointFor(el, params);
      const target = hitTarget(el, x, y);
      startWatch(el);
      for (const type of ["pointerover", "pointerenter", "mouseover", "mouseenter", "pointermove", "mousemove"]) {
        mouseEvent(target, type, x, y);
      }
      return { target: A.describe(el) };
    };

    function isTextField(el) {
      if (el.localName === "textarea") {
        return true;
      }
      if (el.localName !== "input") {
        return false;
      }
      const type = (el.type || "text").toLowerCase();
      return !["checkbox", "radio", "button", "submit", "reset", "image", "file", "range", "color", "hidden"].includes(type);
    }

    function editableTarget(el) {
      if (isTextField(el) || el.isContentEditable) {
        return el;
      }
      const inner = el.querySelector("input, textarea, [contenteditable=''], [contenteditable=true]");
      if (inner && (isTextField(inner) || inner.isContentEditable)) {
        return inner;
      }
      throw new AgentError("not_editable", `The ${A.describe(el)} doesn't accept text.`);
    }

    function selectAllIn(el) {
      if (isTextField(el)) {
        try {
          el.select();
        } catch (e) {}
        return;
      }
      const range = document.createRange();
      range.selectNodeContents(el);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }

    function caretToEnd(el) {
      if (isTextField(el)) {
        try {
          const len = el.value.length;
          el.setSelectionRange(len, len);
        } catch (e) {}
        return;
      }
      const range = document.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }

    function fire(el, type, init = {}) {
      const Ctor = type === "input" || type === "beforeinput" ? InputEvent : Event;
      return el.dispatchEvent(new Ctor(type, { bubbles: true, cancelable: type === "beforeinput", composed: true, ...init }));
    }

    // Insert text at the caret, preferring Firefox's editor so the value, undo stack and input
    // events all behave as if typed. Returns how it was inserted.
    function insertText(el, text) {
      let ok = false;
      try {
        ok = document.execCommand("insertText", false, text);
      } catch (e) {}
      if (ok) {
        return "editor";
      }
      if (isTextField(el)) {
        const start = el.selectionStart ?? el.value.length;
        const end = el.selectionEnd ?? start;
        el.value = el.value.slice(0, start) + text + el.value.slice(end);
        try {
          el.setSelectionRange(start + text.length, start + text.length);
        } catch (e) {}
      } else {
        const sel = window.getSelection();
        if (sel.rangeCount) {
          const range = sel.getRangeAt(0);
          range.deleteContents();
          range.insertNode(document.createTextNode(text));
          range.collapse(false);
        } else {
          el.append(text);
        }
      }
      fire(el, "input", { inputType: "insertText", data: text });
      return "value";
    }

    function deleteSelection(el) {
      let ok = false;
      try {
        ok = document.execCommand("delete", false);
      } catch (e) {}
      if (ok) {
        return;
      }
      if (isTextField(el)) {
        el.value = "";
      } else {
        el.textContent = "";
      }
      fire(el, "input", { inputType: "deleteContentBackward" });
    }

    const KEYS = {
      enter: { key: "Enter", code: "Enter", keyCode: 13 },
      tab: { key: "Tab", code: "Tab", keyCode: 9 },
      escape: { key: "Escape", code: "Escape", keyCode: 27 },
      esc: { key: "Escape", code: "Escape", keyCode: 27 },
      backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
      delete: { key: "Delete", code: "Delete", keyCode: 46 },
      space: { key: " ", code: "Space", keyCode: 32 },
      arrowup: { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
      arrowdown: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
      arrowleft: { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
      arrowright: { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
      up: { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
      down: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
      left: { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
      right: { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
      home: { key: "Home", code: "Home", keyCode: 36 },
      end: { key: "End", code: "End", keyCode: 35 },
      pageup: { key: "PageUp", code: "PageUp", keyCode: 33 },
      pagedown: { key: "PageDown", code: "PageDown", keyCode: 34 },
    };

    // Key codes and codes for punctuation on a US keyboard (shifted symbols use their key's code).
    const PUNCTUATION = {
      ".": [190, "Period"], ">": [190, "Period"], ",": [188, "Comma"], "<": [188, "Comma"],
      "/": [191, "Slash"], "?": [191, "Slash"], ";": [186, "Semicolon"], ":": [186, "Semicolon"],
      "'": [222, "Quote"], '"': [222, "Quote"], "[": [219, "BracketLeft"], "{": [219, "BracketLeft"],
      "]": [221, "BracketRight"], "}": [221, "BracketRight"], "\\": [220, "Backslash"], "|": [220, "Backslash"],
      "-": [189, "Minus"], _: [189, "Minus"], "=": [187, "Equal"], "+": [187, "Equal"],
      "`": [192, "Backquote"], "~": [192, "Backquote"], "!": [49, "Digit1"], "@": [50, "Digit2"],
      "#": [51, "Digit3"], $: [52, "Digit4"], "%": [53, "Digit5"], "^": [54, "Digit6"],
      "&": [55, "Digit7"], "*": [56, "Digit8"], "(": [57, "Digit9"], ")": [48, "Digit0"],
    };

    // Editors such as Google Docs read keystrokes from a tiny, invisible editable element and
    // ignore text simply inserted into it; those need key-by-key typing.
    function isHiddenInput(el) {
      // Google Docs puts its input in a 1-pixel, transparent iframe far above the page.
      if (window !== window.top && (window.innerWidth <= 2 || window.innerHeight <= 2)) {
        return true;
      }
      const r = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return r.width <= 2 || r.height <= 2 || Number(style.opacity) === 0 || A.isOffscreen(el);
    }

    function keyFor(name) {
      const known = KEYS[name.toLowerCase()];
      if (known) {
        return known;
      }
      if (/^f([1-9]|1[0-2])$/i.test(name)) {
        const n = parseInt(name.slice(1), 10);
        return { key: name.toUpperCase(), code: name.toUpperCase(), keyCode: 111 + n };
      }
      if ([...name].length === 1) {
        const upper = name.toUpperCase();
        const isLetter = /[a-z]/i.test(name);
        const isDigit = /[0-9]/.test(name);
        const punct = PUNCTUATION[name];
        return {
          key: name,
          code: isLetter ? `Key${upper}` : isDigit ? `Digit${name}` : punct ? punct[1] : "",
          keyCode: isLetter || isDigit ? upper.charCodeAt(0) : punct ? punct[0] : 0,
          printable: true,
        };
      }
      throw new AgentError("bad_key", `Unknown key ${name}.`);
    }

    function parseChord(chord) {
      const parts = chord.split("+").map(p => p.trim()).filter(Boolean);
      if (chord.endsWith("++")) {
        parts.push("+");
      }
      const keyName = parts.pop();
      return { mods: modifiersOf(parts), key: keyFor(keyName === "Space" ? "space" : keyName) };
    }

    function keyEvent(target, type, key, mods) {
      const printable = key.printable || key.key === " " || key.key === "Enter";
      const charCode = type === "keypress" && printable ? (key.key === "Enter" ? 13 : key.key.charCodeAt(0)) : 0;
      return target.dispatchEvent(
        new KeyboardEvent(type, {
          key: key.key,
          code: key.code,
          keyCode: type === "keypress" ? (charCode ? 0 : key.keyCode) : key.keyCode,
          charCode,
          which: charCode || key.keyCode,
          bubbles: true,
          cancelable: true,
          composed: true,
          ctrlKey: !!mods.ctrl,
          shiftKey: !!mods.shift,
          altKey: !!mods.alt,
          metaKey: !!mods.meta,
          view: window,
        })
      );
    }

    function tabbables() {
      const all = Array.from(
        document.querySelectorAll("a[href], button, input, select, textarea, [tabindex], [contenteditable]")
      ).filter(el => el.tabIndex >= 0 && !el.disabled && !A.isHidden(el));
      const positive = all.filter(el => el.tabIndex > 0).sort((a, b) => a.tabIndex - b.tabIndex);
      return positive.concat(all.filter(el => el.tabIndex === 0));
    }

    function submitFrom(el) {
      const form = el.form || (el.closest && el.closest("form"));
      if (form) {
        if (typeof form.requestSubmit === "function") {
          form.requestSubmit();
        } else {
          form.submit();
        }
        return true;
      }
      return false;
    }

    // Emulates what the browser would do for a key that the page didn't cancel.
    function defaultAction(target, key, mods) {
      const editable = isTextField(target) || target.isContentEditable;
      switch (key.key) {
        case "Enter":
          if (target.localName === "textarea" || target.isContentEditable) {
            insertText(target, "\n");
            return "inserted a new line";
          }
          if (target.localName === "input") {
            return submitFrom(target) ? "submitted the form" : null;
          }
          if (target.localName === "a" || target.localName === "button" || A.roleOf(target) === "button") {
            target.click();
            return "activated it";
          }
          return null;
        case "Tab": {
          const list = tabbables();
          if (!list.length) {
            return null;
          }
          const i = list.indexOf(target);
          const next = mods.shift ? list[(i <= 0 ? list.length : i) - 1] : list[(i + 1) % list.length];
          next.focus();
          return `moved focus to ${A.describe(next)}`;
        }
        case " ":
          if (!editable && (target.localName === "button" || ["checkbox", "radio"].includes(target.type))) {
            target.click();
            return "activated it";
          }
          if (editable) {
            insertText(target, " ");
            return "typed a space";
          }
          return null;
        case "Backspace":
        case "Delete":
          if (editable) {
            try {
              document.execCommand(key.key === "Backspace" ? "delete" : "forwardDelete", false);
            } catch (e) {}
            return "deleted";
          }
          return null;
        case "ArrowUp":
        case "ArrowDown":
          if (target.localName === "select") {
            const step = key.key === "ArrowDown" ? 1 : -1;
            const next = Math.max(0, Math.min(target.options.length - 1, target.selectedIndex + step));
            if (next !== target.selectedIndex) {
              target.selectedIndex = next;
              fire(target, "input");
              fire(target, "change");
            }
            return `selected ${A.quote(target.options[next] ? target.options[next].text : "")}`;
          }
          return null;
        default:
          if ((mods.ctrl || mods.meta) && key.key.toLowerCase() === "a") {
            if (editable) {
              selectAllIn(target);
            } else {
              try {
                document.execCommand("selectAll", false);
              } catch (e) {}
            }
            return "selected all";
          }
          if (key.printable && editable && !mods.ctrl && !mods.meta && !mods.alt) {
            insertText(target, key.key);
            return null;
          }
          return null;
      }
    }

    A.pressKey = async function (params) {
      markActing();
      let target = params.node != null || params.selector ? A.resolveTarget(params) : A.deepActiveElement();
      if (!target || target === document.documentElement) {
        target = document.body;
      }
      if ((params.node != null || params.selector) && typeof target.focus === "function") {
        target.focus({ preventScroll: true });
      }
      startWatch(target);
      const results = [];
      for (const chord of String(params.keys).split(/\s+/).filter(Boolean)) {
        const { mods, key } = parseChord(chord);
        const current = A.deepActiveElement() || target;
        const allowed = keyEvent(current, "keydown", key, mods);
        if (allowed && (key.printable || key.key === "Enter" || key.key === " ")) {
          keyEvent(current, "keypress", key, mods);
        }
        let did = null;
        if (allowed) {
          did = defaultAction(current, key, mods);
        }
        keyEvent(current, "keyup", key, mods);
        results.push(`${chord}: ${allowed ? did || "no default action" : "the page handled it"}`);
      }
      return { target: A.describe(target), keys: results };
    };

    A.type = async function (params) {
      markActing();
      const el = editableTarget(A.resolveTarget(params));
      ensureInView(el);
      el.focus({ preventScroll: true });
      startWatch(el);
      const text = String(params.text ?? "");
      if (params.clear !== false) {
        selectAllIn(el);
        if (!text) {
          deleteSelection(el);
        }
      } else {
        caretToEnd(el);
      }
      let how = "editor";
      const byKeys = params.method === "keys" || (params.method !== "insert_only" && isHiddenInput(el));
      if (byKeys && params.method !== "keys") {
        how = "keys_auto";
      }
      if (byKeys) {
        for (const ch of text) {
          const key = ch === "\n" ? KEYS.enter : keyFor(ch);
          const allowed = keyEvent(el, "keydown", key, {});
          if (allowed) {
            keyEvent(el, "keypress", key, {});
            how = insertText(el, ch);
          }
          keyEvent(el, "keyup", key, {});
        }
      } else if (text) {
        how = insertText(el, text);
      }
      if (isTextField(el)) {
        fire(el, "change");
      }
      let submitted = null;
      if (params.submit) {
        const allowed = keyEvent(el, "keydown", KEYS.enter, {});
        if (allowed) {
          keyEvent(el, "keypress", KEYS.enter, {});
          submitted = submitFrom(el) ? "form submitted" : "pressed Enter (no form to submit)";
        } else {
          submitted = "pressed Enter; the page handled it";
        }
        keyEvent(el, "keyup", KEYS.enter, {});
      }
      const hidden = isHiddenInput(el);
      return {
        target: A.describe(el),
        // A hidden input's own value says nothing about what the page shows.
        value: hidden ? null : A.valueOf(el),
        method: byKeys && params.method !== "keys" ? "keys_auto" : how,
        hiddenInput: hidden,
        submitted,
      };
    };

    // Firefox records who put each item in a DataTransfer and hides items added by the extension
    // from the page, so drag data and dropped files are built with the page's own constructors.
    function pageDataTransfer(files = []) {
      const page = window.wrappedJSObject;
      const dt = page ? new page.DataTransfer() : new DataTransfer();
      for (const f of files) {
        dt.items.add(f);
      }
      return dt;
    }

    function dragEvent(target, type, x, y, dataTransfer) {
      const init = {
        bubbles: true,
        cancelable: type !== "dragleave" && type !== "dragend",
        composed: true,
        clientX: x,
        clientY: y,
        dataTransfer,
      };
      const page = window.wrappedJSObject;
      const event = page
        ? new page.DragEvent(type, cloneInto(init, window, { wrapReflectors: true }))
        : new DragEvent(type, init);
      return target.dispatchEvent(event);
    }

    function fileInputFor(el) {
      if (el.localName === "input" && (el.type || "").toLowerCase() === "file") {
        return el;
      }
      const inner = el.querySelector && el.querySelector("input[type=file]");
      if (inner) {
        return inner;
      }
      const label = el.closest && el.closest("label");
      if (label && label.control && label.control.type === "file") {
        return label.control;
      }
      return null;
    }

    A.uploadFiles = async function (params) {
      markActing();
      const el = A.resolveTarget(params);
      const files = (params.files || []).map(f => new File([f.bytes], f.name, { type: f.type }));
      if (!files.length) {
        throw new AgentError("no_files", "No files to upload.");
      }
      const dt = pageDataTransfer(files);
      const names = files.map(f => `${f.name} (${f.size} bytes)`);
      startWatch(el);
      const input = fileInputFor(el);
      if (input) {
        if (files.length > 1 && !input.multiple) {
          throw new AgentError("single_file", `That file input takes one file; you passed ${files.length}.`);
        }
        input.files = dt.files;
        fire(input, "input");
        fire(input, "change");
        return { target: A.describe(input), how: "set the file input", names };
      }
      ensureInView(el);
      const r = el.getBoundingClientRect();
      const x = r.left + r.width / 2;
      const y = r.top + r.height / 2;
      dragEvent(el, "dragenter", x, y, dt);
      const accepted = !dragEvent(el, "dragover", x, y, dt);
      dragEvent(el, "drop", x, y, dt);
      return {
        target: A.describe(el),
        how: accepted ? "dropped the files on it" : "dropped the files on it, though it didn't signal that it accepts drops",
        names,
      };
    };

    A.drag = async function (params) {
      markActing();
      const from = A.resolveTarget(params);
      const to = A.resolveTarget({ node: params.to_node, doc: params.to_doc, selector: params.to_selector });
      const start = pointFor(from, {});
      const source = hitTarget(from, start.x, start.y);
      ensureInView(to);
      const tr = to.getBoundingClientRect();
      const end = { x: tr.left + tr.width / 2, y: tr.top + tr.height / 2 };
      startWatch(from);
      const draggable = from.closest("[draggable=true]") || (["a", "img"].includes(from.localName) ? from : null);
      if (draggable && params.method !== "pointer") {
        const dt = pageDataTransfer();
        if (!dragEvent(draggable, "dragstart", start.x, start.y, dt)) {
          return { from: A.describe(from), to: A.describe(to), how: "the page cancelled the drag" };
        }
        dragEvent(draggable, "drag", start.x, start.y, dt);
        const dropTarget = elementAt(end.x, end.y) || to;
        dragEvent(dropTarget, "dragenter", end.x, end.y, dt);
        const accepted = !dragEvent(dropTarget, "dragover", end.x, end.y, dt);
        if (accepted) {
          dragEvent(dropTarget, "drop", end.x, end.y, dt);
        }
        dragEvent(draggable, "dragend", end.x, end.y, dt);
        return {
          from: A.describe(from),
          to: A.describe(to),
          how: accepted ? "dragged and dropped (HTML drag and drop)" : "dragged, but the target didn't accept the drop",
        };
      }
      mouseEvent(source, "pointerdown", start.x, start.y, { buttons: 1 });
      mouseEvent(source, "mousedown", start.x, start.y, { buttons: 1 });
      const steps = 10;
      for (let i = 1; i <= steps; i++) {
        const x = start.x + ((end.x - start.x) * i) / steps;
        const y = start.y + ((end.y - start.y) * i) / steps;
        const over = elementAt(x, y) || to;
        mouseEvent(over, "pointermove", x, y, { buttons: 1 });
        mouseEvent(over, "mousemove", x, y, { buttons: 1 });
        await A.sleep(16);
      }
      const over = elementAt(end.x, end.y) || to;
      mouseEvent(over, "pointerup", end.x, end.y, {});
      mouseEvent(over, "mouseup", end.x, end.y, {});
      return { from: A.describe(from), to: A.describe(to), how: "dragged with the pointer" };
    };

    A.selectOption = async function (params) {
      markActing();
      const el = A.resolveTarget(params);
      if (el.localName !== "select") {
        throw new AgentError(
          "not_a_select",
          `The ${A.describe(el)} isn't a native select. Click it to open it, then click the option.`
        );
      }
      const values = (params.values || []).map(String);
      const labels = (params.labels || []).map(l => String(l).trim().toLowerCase());
      const matches = Array.from(el.options).filter(
        o => values.includes(o.value) || labels.includes(o.text.trim().toLowerCase())
      );
      if (!matches.length) {
        const available = Array.from(el.options).slice(0, 20).map(o => A.quote(A.clean(o.text, 40))).join(", ");
        throw new AgentError("no_such_option", `No matching option. Options: ${available}`);
      }
      startWatch(el);
      el.focus({ preventScroll: true });
      if (el.multiple) {
        for (const o of el.options) {
          o.selected = matches.includes(o);
        }
      } else {
        el.value = matches[0].value;
      }
      fire(el, "input");
      fire(el, "change");
      return { target: A.describe(el), selected: Array.from(el.selectedOptions).map(o => o.text) };
    };

    A.scroll = async function (params) {
      markActing();
      startWatch();
      if ((params.node != null || params.selector) && !params.direction) {
        const el = A.resolveTarget(params);
        el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
        return { target: A.describe(el) };
      }
      const container = params.node != null || params.selector ? A.resolveTarget(params) : document.scrollingElement || document.documentElement;
      const vertical = !["left", "right"].includes(params.direction);
      const sign = ["up", "left"].includes(params.direction) ? -1 : 1;
      const view = container === document.scrollingElement ? (vertical ? window.innerHeight : window.innerWidth) : vertical ? container.clientHeight : container.clientWidth;
      const amount = sign * (params.amount || Math.round(view * 0.85));
      const r = container.getBoundingClientRect();
      const x = container === document.scrollingElement ? window.innerWidth / 2 : r.left + r.width / 2;
      const y = container === document.scrollingElement ? window.innerHeight / 2 : r.top + r.height / 2;
      container.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y,
          deltaY: vertical ? amount : 0, deltaX: vertical ? 0 : amount, deltaMode: 0, view: window,
        })
      );
      const before = vertical ? container.scrollTop : container.scrollLeft;
      container.scrollBy({ top: vertical ? amount : 0, left: vertical ? 0 : amount, behavior: "instant" });
      const after = vertical ? container.scrollTop : container.scrollLeft;
      const max = vertical ? container.scrollHeight - container.clientHeight : container.scrollWidth - container.clientWidth;
      return {
        moved: Math.round(after - before),
        position: Math.round(after),
        max: Math.round(max),
        atEnd: sign > 0 ? after >= max - 2 : after <= 2,
      };
    };
  })(AgentF);
}
