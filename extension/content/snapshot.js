/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

var AgentF = typeof AgentF !== "undefined" ? AgentF : {};

{
  (function (A) {
    const MAX_ELEMENTS = 25000;

    const STRUCTURAL = new Set([
      "banner", "navigation", "main", "complementary", "contentinfo", "search", "form", "region",
      "dialog", "alertdialog", "alert", "status", "heading", "tablist", "tabpanel", "menu", "menubar",
      "toolbar", "tree", "grid", "radiogroup", "iframe",
    ]);
    const FULL_ONLY = new Set([
      "list", "listitem", "table", "row", "cell", "columnheader", "rowheader", "paragraph", "img",
      "article", "figure", "blockquote", "group", "separator", "progressbar", "meter", "video", "audio",
    ]);
    // Roles whose name already carries their text; their subtree adds nothing in interactive mode.
    // Headings aren't among them: sites put whole toolbars inside one (Google Docs does).
    const LEAF = new Set([
      "link", "button", "checkbox", "radio", "textbox", "searchbox", "spinbutton", "slider", "switch",
      "option", "menuitem", "menuitemcheckbox", "menuitemradio", "img", "clickable",
      "separator", "progressbar", "meter",
    ]);
    const COLLAPSE_PRIORITY = {
      contentinfo: 0, navigation: 1, complementary: 2, banner: 3, list: 4, menu: 4, listbox: 4,
      tree: 4, table: 4, grid: 4, toolbar: 4, tablist: 5, region: 5, group: 5, form: 5, article: 5,
      search: 5,
    };

    function build(root, opts) {
      const ctx = {
        full: opts.mode === "full",
        viewportOnly: !!opts.viewport_only,
        count: 0,
        truncated: false,
      };
      const out = [];
      walk(root, ctx, out, { inInteractive: false, inNamed: false }, true);
      return { nodes: out, truncated: ctx.truncated };
    }

    function walk(node, ctx, out, state, isRoot) {
      if (ctx.count++ > MAX_ELEMENTS) {
        ctx.truncated = true;
        return;
      }
      if (node.nodeType === Node.TEXT_NODE) {
        if (ctx.full && !state.inNamed) {
          const text = A.clean(node.data, 200);
          if (text) {
            out.push({ text });
          }
        }
        return;
      }
      if (node.nodeType === Node.DOCUMENT_NODE) {
        walk(node.body || node.documentElement, ctx, out, state, false);
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) {
        return;
      }
      if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node;
        if (A.isHidden(el)) {
          return;
        }
        const role = A.roleOf(el);
        if (role === "iframe" && (el.localName === "iframe" || el.localName === "frame")) {
          let frame = -1;
          try {
            frame = browser.runtime.getFrameId(el);
          } catch (e) {}
          out.push({ role: "iframe", name: A.clean(el.title || el.name || ""), frame, el });
          return;
        }
        const clickable = !role && !state.inInteractive && A.isClickable(el, role);
        const effectiveRole = role || (clickable ? "clickable" : null);
        const interactive = A.INTERACTIVE.has(effectiveRole) && !(effectiveRole === "clickable" && state.inInteractive);
        const named = (effectiveRole === "group" || effectiveRole === "region") && A.nameOf(el, effectiveRole);
        const keep =
          isRoot ||
          interactive ||
          STRUCTURAL.has(effectiveRole) ||
          (effectiveRole === "listbox") ||
          (ctx.full && FULL_ONLY.has(effectiveRole)) ||
          named;
        if (keep && effectiveRole) {
          const name = A.nameOf(el, effectiveRole);
          const item = { role: effectiveRole, name, states: A.statesOf(el, effectiveRole), children: [], el };
          if (interactive) {
            item.id = A.remember(el, effectiveRole, name);
            item.offscreen = A.isOffscreen(el);
          }
          if (!LEAF.has(effectiveRole) || ctx.full) {
            const childState = {
              inInteractive: state.inInteractive || interactive,
              inNamed: state.inNamed || (interactive && !!name) || effectiveRole === "heading",
            };
            for (const c of A.childNodesOf(el)) {
              walk(c, ctx, item.children, childState, false);
            }
          }
          if (ctx.viewportOnly && !item.children.length && (interactive ? item.offscreen : A.isOffscreen(el))) {
            return;
          }
          if (!interactive && !item.children.length && !item.name && !isRoot) {
            return;
          }
          out.push(item);
          return;
        }
      }
      for (const c of A.childNodesOf(node)) {
        walk(c, ctx, out, state, false);
      }
    }

    function lineFor(n, depth) {
      const indent = "  ".repeat(depth);
      if (n.text !== undefined) {
        return `${indent}- text: ${A.quote(n.text)}`;
      }
      let line = `${indent}- ${n.role}`;
      if (n.name) {
        line += ` ${A.quote(n.name)}`;
      }
      if (n.id) {
        line += ` [ref=${A.refToken(n.id)}]`;
      }
      for (const s of n.states || []) {
        line += ` [${s}]`;
      }
      if (n.offscreen) {
        line += " (offscreen)";
      }
      if (n.collapsed) {
        line += ` (collapsed: ${n.collapsed}; pass this ref as root to expand)`;
      }
      return line;
    }

    function measure(n, depth) {
      n.depth = depth;
      n.ownSize = lineFor(n, depth).length + 1;
      n.size = n.ownSize;
      n.interactiveCount = n.id ? 1 : 0;
      for (const c of n.children || []) {
        measure(c, depth + 1);
        n.size += c.size;
        n.interactiveCount += c.interactiveCount;
      }
    }

    function applyBudget(nodes, budget) {
      let total = 0;
      for (const n of nodes) {
        measure(n, 0);
        total += n.size;
      }
      if (total <= budget) {
        return;
      }
      const candidates = [];
      const collect = (list, parent) => {
        for (const n of list) {
          n.parent = parent;
          if (n.children && n.children.length && n.role in COLLAPSE_PRIORITY) {
            candidates.push(n);
          }
          collect(n.children || [], n);
        }
      };
      collect(nodes, null);
      candidates.sort((a, b) => COLLAPSE_PRIORITY[a.role] - COLLAPSE_PRIORITY[b.role] || b.size - a.size);
      const collapsedAncestor = n => {
        for (let p = n.parent; p; p = p.parent) {
          if (p.collapsed) {
            return true;
          }
        }
        return false;
      };
      for (const n of candidates) {
        if (total <= budget) {
          break;
        }
        if (n.size < 200 || collapsedAncestor(n)) {
          continue;
        }
        const items = n.interactiveCount;
        n.collapsed = `${items} interactive item${items === 1 ? "" : "s"}`;
        if (!n.id) {
          n.id = A.remember(n.el, n.role, n.name);
        }
        const newSize = lineFor(n, n.depth).length + 1;
        for (let p = n.parent; p; p = p.parent) {
          p.size -= n.size - newSize;
        }
        total -= n.size - newSize;
        n.size = newSize;
      }
    }

    function serialize(nodes, depth, lines) {
      for (const n of nodes) {
        if (n.frame !== undefined) {
          lines.push(`${"  ".repeat(depth)}- iframe${n.name ? " " + A.quote(n.name) : ""}`);
          if (n.frame >= 0) {
            lines.push(`${A.FRAME_MARK}${n.frame}:${depth + 1}${A.FRAME_MARK}`);
          }
          continue;
        }
        lines.push(lineFor(n, depth));
        if (!n.collapsed && n.children) {
          serialize(n.children, depth + 1, lines);
        }
      }
    }

    A.snapshot = function (params) {
      const root = params.node != null || params.selector ? A.resolveTarget(params) : document;
      const budget = params.max_chars || 12000;
      const { nodes, truncated } = build(root, params);
      applyBudget(nodes, budget);
      const lines = [];
      serialize(nodes, params.indent || 0, lines);
      let text = lines.join("\n");
      let cut = false;
      if (text.length > budget) {
        text = text.slice(0, budget);
        text = text.slice(0, text.lastIndexOf("\n"));
        text += "\n- (the outline stops here to fit max_chars; use find, or pass a section's ref as root)";
        cut = true;
      }
      return {
        text,
        truncated: truncated || cut,
        url: location.href,
        title: document.title,
        doc: A.DOC_ID,
      };
    };

    function allSemantic(root, filter, limit) {
      const matches = [];
      const visit = node => {
        if (matches.length >= limit) {
          return;
        }
        if (node.nodeType === Node.ELEMENT_NODE) {
          if (A.isHidden(node)) {
            return;
          }
          const role = A.roleOf(node) || (A.isClickable(node, null) ? "clickable" : null);
          if (role && filter(node, role)) {
            matches.push({ el: node, role });
          }
        }
        for (const c of A.childNodesOf(node)) {
          visit(c);
        }
      };
      visit(root);
      return matches;
    }

    function matchLine(el, role) {
      const name = A.nameOf(el, role);
      const id = A.remember(el, role, name);
      let line = `- ${role}${name ? " " + A.quote(name) : ""} [ref=${A.refToken(id)}]`;
      for (const s of A.statesOf(el, role)) {
        line += ` [${s}]`;
      }
      if (A.isOffscreen(el)) {
        line += " (offscreen)";
      }
      if (!name) {
        const text = A.textOf(el, 100);
        if (text) {
          line += ` text=${A.quote(text)}`;
        }
      }
      return line;
    }

    A.find = function (params) {
      const limit = params.limit || 10;
      const wantRole = params.role ? params.role.toLowerCase() : null;
      const wantName = params.name ? params.name.toLowerCase() : null;
      const wantText = params.text ? params.text.toLowerCase() : null;
      let pool;
      if (params.selector) {
        let found;
        try {
          found = Array.from(document.querySelectorAll(params.selector));
        } catch (e) {
          throw new A.AgentError("bad_selector", `${params.selector} is not a valid CSS selector.`);
        }
        pool = found
          .filter(el => !A.isHidden(el))
          .map(el => ({ el, role: A.roleOf(el) || (A.isClickable(el, null) ? "clickable" : el.localName) }));
      } else {
        pool = allSemantic(document.body || document.documentElement, () => true, 5000);
      }
      const lines = [];
      let total = 0;
      for (const { el, role } of pool) {
        if (wantRole && role !== wantRole) {
          continue;
        }
        if (wantName && !A.nameOf(el, role).toLowerCase().includes(wantName)) {
          continue;
        }
        if (wantText && !A.textOf(el, 2000).toLowerCase().includes(wantText)) {
          continue;
        }
        if (!wantRole && !wantName && !params.selector && !A.INTERACTIVE.has(role)) {
          continue;
        }
        total++;
        if (lines.length < limit) {
          lines.push(matchLine(el, role));
        }
      }
      return { text: lines.join("\n"), total, doc: A.DOC_ID };
    };

    A.candidates = function (role, name) {
      if (!role) {
        return [];
      }
      const matches = allSemantic(
        document.body || document.documentElement,
        (el, r) => r === role && A.nameOf(el, r) === name,
        3
      );
      return matches.map(m => matchLine(m.el, m.role));
    };

    A.marks = function () {
      const { nodes } = build(document, { mode: "interactive", viewport_only: true });
      const marks = [];
      const collect = list => {
        for (const n of list) {
          if (n.id && !n.offscreen) {
            const r = n.el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
              marks.push({ token: A.refToken(n.id), x: r.left, y: r.top, w: r.width, h: r.height });
            }
          }
          collect(n.children || []);
        }
      };
      collect(nodes);
      return {
        marks,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        scroll: { x: window.scrollX, y: window.scrollY },
        dpr: window.devicePixelRatio,
      };
    };

    A.geometry = function (params) {
      const doc = document.documentElement;
      const out = {
        viewport: { width: window.innerWidth, height: window.innerHeight },
        scroll: { x: window.scrollX, y: window.scrollY },
        page: { width: Math.max(doc.scrollWidth, doc.clientWidth), height: Math.max(doc.scrollHeight, doc.clientHeight) },
        dpr: window.devicePixelRatio,
      };
      if (params.node != null || params.selector) {
        const el = A.resolveTarget(params);
        const r = el.getBoundingClientRect();
        out.element = { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height };
      }
      return out;
    };
  })(AgentF);
}
