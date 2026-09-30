/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

// All content files share one namespace. The background page injects them again when a page's
// copy is out of date (it was open before the add-on loaded or updated), so each file redefines
// its functions every time it runs, but keeps page state (refs, listeners) from earlier runs.
var AgentF = typeof AgentF !== "undefined" ? AgentF : {};
// Set by the background page just before it injects these files; absent when Firefox loads them.
var AgentFBuild = typeof AgentFBuild !== "undefined" ? AgentFBuild : null;

{
  (function (A) {
    A.build = AgentFBuild;
    A.DOC_ID = A.DOC_ID || Math.random().toString(16).slice(2, 10);
    A.REF_MARK = "\u0001";
    A.FRAME_MARK = "\u0002";

    class AgentError extends Error {
      constructor(code, message, data) {
        super(message);
        this.code = code;
        this.data = data;
      }
    }
    A.AgentError = AgentError;

    // Element refs. The broker turns these local ids into global refs such as e57.
    A.refs = A.refs || { registry: new Map(), idOf: new WeakMap(), next: 1 };
    const registry = A.refs.registry;
    const idOf = A.refs.idOf;

    A.remember = function (el, role, name) {
      let id = idOf.get(el);
      if (!id) {
        id = A.refs.next++;
        idOf.set(el, id);
      }
      registry.set(id, { ref: new WeakRef(el), role, name });
      return id;
    };

    A.refToken = id => `${A.REF_MARK}${A.DOC_ID}:${id}${A.REF_MARK}`;

    A.lookup = function (node, doc) {
      if (doc !== A.DOC_ID) {
        throw new AgentError(
          "stale_document",
          "The page changed (it navigated or reloaded) since that ref was issued."
        );
      }
      const record = registry.get(node);
      const el = record && record.ref.deref();
      if (!el || !el.isConnected) {
        const what = record ? `${record.role} ${A.quote(record.name)}` : "element";
        const candidates = record ? A.candidates(record.role, record.name) : [];
        throw new AgentError("node_gone", `The ${what} is no longer on the page.`, { candidates });
      }
      return el;
    };

    A.resolveTarget = function (params) {
      if (params.node !== undefined && params.node !== null) {
        return A.lookup(params.node, params.doc);
      }
      if (params.selector) {
        let el;
        try {
          el = A.deepQuery(params.selector);
        } catch (e) {
          throw new AgentError("bad_selector", `${params.selector} is not a valid CSS selector.`);
        }
        if (!el) {
          throw new AgentError("no_match", `Nothing on the page matches ${params.selector}.`);
        }
        return el;
      }
      throw new AgentError("no_target", "Pass ref or selector.");
    };

    A.deepQuery = function (selector, root = document) {
      const direct = root.querySelector(selector);
      if (direct) {
        return direct;
      }
      for (const el of root.querySelectorAll("*")) {
        const shadow = el.openOrClosedShadowRoot;
        if (shadow) {
          const found = A.deepQuery(selector, shadow);
          if (found) {
            return found;
          }
        }
      }
      return null;
    };

    // Text helpers.
    A.clean = function (text, limit = 150) {
      let t = String(text ?? "")
        .replace(/[\u0001\u0002]/g, "")
        .replace(/\s+/g, " ")
        .trim();
      if (t.length > limit) {
        t = t.slice(0, limit - 1) + "\u2026";
      }
      return t;
    };

    A.quote = text => `"${String(text ?? "").replace(/"/g, "'")}"`;

    A.shortUrl = function (href) {
      try {
        const url = new URL(href, location.href);
        if (url.origin === location.origin) {
          return A.clean(url.pathname + url.search + url.hash, 100) || "/";
        }
        return A.clean(url.href, 100);
      } catch (e) {
        return A.clean(href, 100);
      }
    };

    // The composed tree: shadow roots (open or closed) and slotted content, as rendered.
    A.childNodesOf = function (node) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        const shadow = node.openOrClosedShadowRoot;
        if (shadow) {
          return Array.from(shadow.childNodes);
        }
        if (node.localName === "slot") {
          const assigned = node.assignedNodes({ flatten: true });
          if (assigned.length) {
            return assigned;
          }
        }
        if (node.localName === "template") {
          return [];
        }
      }
      return Array.from(node.childNodes);
    };

    const SKIP_TAGS = new Set(["script", "style", "noscript", "template", "head", "meta", "link", "title"]);
    A.SKIP_TAGS = SKIP_TAGS;

    A.isHidden = function (el) {
      if (SKIP_TAGS.has(el.localName)) {
        return true;
      }
      if (el.hidden || el.getAttribute("aria-hidden") === "true") {
        return true;
      }
      try {
        if (el.checkVisibility({ visibilityProperty: true, checkVisibilityCSS: true })) {
          return false;
        }
        // display: contents elements have no box, so checkVisibility() is false for them even
        // when their children are visible (Slack wraps its whole app in one).
        return getComputedStyle(el).display !== "contents";
      } catch (e) {
        return false;
      }
    };

    A.isOffscreen = function (el) {
      // A tab opened in the background may not be laid out yet; its viewport size is then 0.
      if (!window.innerWidth || !window.innerHeight) {
        return false;
      }
      const r = el.getBoundingClientRect();
      return r.bottom < 0 || r.right < 0 || r.top > window.innerHeight || r.left > window.innerWidth;
    };

    A.textOf = function (node, limit = 300) {
      let out = "";
      const visit = n => {
        if (out.length > limit) {
          return;
        }
        if (n.nodeType === Node.TEXT_NODE) {
          out += n.data + " ";
          return;
        }
        if (n.nodeType !== Node.ELEMENT_NODE && n.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) {
          return;
        }
        if (n.nodeType === Node.ELEMENT_NODE) {
          if (SKIP_TAGS.has(n.localName) || n.hidden || n.getAttribute("aria-hidden") === "true") {
            return;
          }
          if (n.localName === "img" || n.localName === "area") {
            out += (n.getAttribute("alt") || "") + " ";
            return;
          }
          // A label that wraps its control names the control; the control's own contents
          // (a select's options, say) aren't part of that name.
          if (n !== node && ["select", "option", "datalist", "textarea", "input"].includes(n.localName)) {
            return;
          }
          const label = n.getAttribute("aria-label");
          if (label && n !== node) {
            out += label + " ";
            return;
          }
        }
        for (const c of A.childNodesOf(n)) {
          visit(c);
        }
      };
      visit(node);
      return A.clean(out, limit);
    };

    // Roles, following the HTML-AAM mapping of elements plus explicit ARIA roles.
    const INTERACTIVE = new Set([
      "link", "button", "checkbox", "radio", "textbox", "searchbox", "combobox", "listbox",
      "option", "slider", "spinbutton", "switch", "tab", "menuitem", "menuitemcheckbox",
      "menuitemradio", "treeitem", "clickable",
    ]);
    const NAME_FROM_CONTENT = new Set([
      "button", "link", "heading", "tab", "menuitem", "menuitemcheckbox", "menuitemradio",
      "option", "checkbox", "radio", "switch", "treeitem", "cell", "columnheader", "rowheader",
      "gridcell", "tooltip", "clickable", "listitem", "row",
    ]);
    A.INTERACTIVE = INTERACTIVE;

    function inSectioning(el) {
      return !!(el.parentElement && el.parentElement.closest("article, aside, main, nav, section"));
    }

    function hasName(el) {
      return !!(el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || el.getAttribute("title"));
    }

    function implicitRole(el) {
      const tag = el.localName;
      switch (tag) {
        case "a":
        case "area":
          return el.hasAttribute("href") ? "link" : null;
        case "button":
        case "summary":
          return "button";
        case "input": {
          const type = (el.getAttribute("type") || "text").toLowerCase();
          if (type === "hidden") {
            return null;
          }
          if (["button", "submit", "reset", "image", "file"].includes(type)) {
            return "button";
          }
          if (type === "checkbox" || type === "radio") {
            return type;
          }
          if (type === "range") {
            return "slider";
          }
          if (type === "number") {
            return "spinbutton";
          }
          if (type === "search") {
            return el.hasAttribute("list") ? "combobox" : "searchbox";
          }
          return el.hasAttribute("list") ? "combobox" : "textbox";
        }
        case "select":
          return el.multiple || el.size > 1 ? "listbox" : "combobox";
        case "option":
          return "option";
        case "textarea":
          return "textbox";
        case "h1":
        case "h2":
        case "h3":
        case "h4":
        case "h5":
        case "h6":
          return "heading";
        case "img":
          return el.getAttribute("alt") === "" ? null : "img";
        case "nav":
          return "navigation";
        case "main":
          return "main";
        case "aside":
          return "complementary";
        case "search":
          return "search";
        case "header":
          return inSectioning(el) ? null : "banner";
        case "footer":
          return inSectioning(el) ? null : "contentinfo";
        case "form":
          return hasName(el) ? "form" : null;
        case "section":
          return hasName(el) ? "region" : null;
        case "article":
          return "article";
        case "ul":
        case "ol":
        case "menu":
          return "list";
        case "li":
          return "listitem";
        case "table":
          return "table";
        case "tr":
          return "row";
        case "th":
          return "columnheader";
        case "td":
          return "cell";
        case "dialog":
          return "dialog";
        case "details":
        case "fieldset":
          return "group";
        case "p":
          return "paragraph";
        case "hr":
          return "separator";
        case "progress":
          return "progressbar";
        case "meter":
          return "meter";
        case "iframe":
        case "frame":
          return "iframe";
        case "figure":
          return "figure";
        case "video":
        case "audio":
          return tag;
        case "blockquote":
          return "blockquote";
      }
      if (el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable)) {
        return "textbox";
      }
      return null;
    }

    A.roleOf = function (el) {
      const explicit = el.getAttribute("role");
      if (explicit) {
        const first = explicit.trim().split(/\s+/)[0];
        if (first === "none" || first === "presentation") {
          return null;
        }
        if (first) {
          return first;
        }
      }
      return implicitRole(el);
    };

    A.isClickable = function (el, role) {
      if (role && role !== "generic" && role !== "listitem" && role !== "img") {
        return false;
      }
      const hinted =
        el.hasAttribute("onclick") ||
        (el.hasAttribute("tabindex") && el.tabIndex >= 0 && el.localName !== "body") ||
        (["div", "span", "li", "img", "svg", "i", "label", "td"].includes(el.localName) &&
          getComputedStyle(el).cursor === "pointer" &&
          !(el.parentElement && getComputedStyle(el.parentElement).cursor === "pointer"));
      if (!hinted) {
        return false;
      }
      // A container of real controls, or a large region, isn't a button; treating it as one
      // would hide everything inside it.
      if (el.querySelector(CONTROL_SELECTOR)) {
        return false;
      }
      const r = el.getBoundingClientRect();
      return r.width * r.height <= 0.2 * window.innerWidth * window.innerHeight;
    };
    const CONTROL_SELECTOR =
      "a[href], button, input, select, textarea, [role=button], [role=link], [role=checkbox], [role=tab], [role=menuitem], [contenteditable]";

    A.nameOf = function (el, role) {
      const labelledby = el.getAttribute("aria-labelledby");
      if (labelledby) {
        const root = el.getRootNode();
        const text = labelledby
          .split(/\s+/)
          .map(id => (root.getElementById ? root.getElementById(id) : null) || document.getElementById(id))
          .filter(Boolean)
          .map(n => A.textOf(n))
          .join(" ");
        if (A.clean(text)) {
          return A.clean(text);
        }
      }
      const aria = el.getAttribute("aria-label");
      if (aria && aria.trim()) {
        return A.clean(aria);
      }
      const tag = el.localName;
      if (tag === "input" || tag === "textarea" || tag === "select") {
        const type = (el.type || "").toLowerCase();
        if (["button", "submit", "reset"].includes(type)) {
          return A.clean(el.value || (type === "submit" ? "Submit" : type === "reset" ? "Reset" : ""));
        }
        if (type === "image") {
          return A.clean(el.alt || el.value || "Submit");
        }
        const labels = el.labels ? Array.from(el.labels).map(l => A.textOf(l)).join(" ") : "";
        if (A.clean(labels)) {
          return A.clean(labels);
        }
        return A.clean(el.getAttribute("placeholder") || el.title || "");
      }
      if (tag === "img" || tag === "area") {
        return A.clean(el.getAttribute("alt") || el.title || "");
      }
      if (tag === "svg") {
        const title = el.querySelector("title");
        return A.clean(title ? title.textContent : "");
      }
      if (tag === "fieldset" || tag === "table" || tag === "figure") {
        const caption = el.querySelector(":scope > legend, :scope > caption, :scope > figcaption");
        if (caption) {
          return A.textOf(caption, 150);
        }
      }
      if (NAME_FROM_CONTENT.has(role) || (role === "heading")) {
        const text = A.textOf(el, 150);
        if (text) {
          return text;
        }
      }
      return A.clean(el.title || "");
    };

    A.isPassword = el => el.localName === "input" && (el.type || "").toLowerCase() === "password";

    A.valueOf = function (el) {
      if (A.isPassword(el)) {
        return el.value ? "redacted" : "";
      }
      if (el.localName === "select") {
        return Array.from(el.selectedOptions).map(o => A.clean(o.text, 60)).join(", ");
      }
      if ("value" in el && el.localName !== "button" && el.localName !== "li") {
        return A.clean(el.value, 80);
      }
      if (el.isContentEditable) {
        return A.textOf(el, 80);
      }
      return "";
    };

    const VALUE_ROLES = new Set(["textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox"]);

    A.statesOf = function (el, role) {
      const s = [];
      const attr = n => el.getAttribute(n);
      if (role === "heading") {
        const level = attr("aria-level") || (el.localName.match(/^h([1-6])$/) || [])[1] || "2";
        s.push(`level=${level}`);
      }
      const checkedAttr = attr("aria-checked");
      if (el.localName === "input" && (el.type === "checkbox" || el.type === "radio")) {
        if (el.indeterminate) {
          s.push("mixed");
        } else if (el.checked) {
          s.push("checked");
        }
      } else if (checkedAttr === "true") {
        s.push("checked");
      } else if (checkedAttr === "mixed") {
        s.push("mixed");
      }
      if (attr("aria-pressed") === "true") {
        s.push("pressed");
      }
      const expanded = attr("aria-expanded");
      if (expanded === "true") {
        s.push("expanded");
      } else if (expanded === "false") {
        s.push("collapsed");
      } else if (el.localName === "details") {
        s.push(el.open ? "expanded" : "collapsed");
      }
      if (attr("aria-selected") === "true" || (el.localName === "option" && el.selected)) {
        s.push("selected");
      }
      if (el.disabled || attr("aria-disabled") === "true") {
        s.push("disabled");
      }
      if (el.required || attr("aria-required") === "true") {
        s.push("required");
      }
      const invalid = attr("aria-invalid");
      if (invalid && invalid !== "false") {
        s.push("invalid");
      }
      const current = attr("aria-current");
      if (current && current !== "false") {
        s.push("current");
      }
      if (VALUE_ROLES.has(role)) {
        const value = A.valueOf(el);
        if (value) {
          s.push(`value=${A.quote(value)}`);
        }
      }
      if (role === "link" && el.href) {
        s.push(`url=${A.shortUrl(el.getAttribute("href"))}`);
      }
      return s;
    };

    A.describe = function (el) {
      const role = A.roleOf(el) || (A.isClickable(el, null) ? "clickable" : el.localName);
      const name = A.nameOf(el, role);
      const id = A.remember(el, role, name);
      let text = "";
      if (!name) {
        const content = A.textOf(el, 80);
        if (content) {
          text = ` containing ${A.quote(content)}`;
        }
      }
      return `${role}${name ? " " + A.quote(name) : ""}${text} [ref=${A.refToken(id)}]`;
    };

    A.deepActiveElement = function () {
      let el = document.activeElement;
      while (el && el.openOrClosedShadowRoot && el.openOrClosedShadowRoot.activeElement) {
        el = el.openOrClosedShadowRoot.activeElement;
      }
      return el;
    };

    A.sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  })(AgentF);
}
