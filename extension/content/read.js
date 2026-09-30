/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

var AgentF = typeof AgentF !== "undefined" ? AgentF : {};

{
  (function (A) {
    const BLOCK = new Set([
      "p", "div", "section", "article", "main", "header", "footer", "aside", "nav", "form",
      "figure", "figcaption", "details", "summary", "dl", "dt", "dd", "address", "fieldset",
    ]);

    // Converts a DOM subtree to Markdown. `live` means it's the real page, so hidden and
    // off-viewport elements can be skipped; Readability output is a detached copy.
    function toMarkdown(root, { live = false, viewportOnly = false, plain = false } = {}) {
      const blocks = [];
      let line = "";

      const flush = () => {
        const t = line.replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").trim();
        if (t) {
          blocks.push(t);
        }
        line = "";
      };
      const skip = el => {
        if (A.SKIP_TAGS.has(el.localName) || el.localName === "svg") {
          return true;
        }
        if (live) {
          if (A.isHidden(el)) {
            return true;
          }
          if (viewportOnly && BLOCK.has(el.localName) && A.isOffscreen(el)) {
            return true;
          }
        }
        return false;
      };
      const inline = node => {
        let out = "";
        for (const c of A.childNodesOf(node)) {
          if (c.nodeType === Node.TEXT_NODE) {
            out += c.data.replace(/\s+/g, " ");
          } else if (c.nodeType === Node.ELEMENT_NODE && !skip(c)) {
            out += inlineElement(c);
          }
        }
        return out;
      };
      const inlineElement = el => {
        const tag = el.localName;
        const text = inline(el);
        if (plain) {
          return tag === "br" ? "\n" : tag === "img" ? "" : text;
        }
        switch (tag) {
          case "a": {
            const href = el.getAttribute("href");
            const t = text.trim();
            if (!href || href.startsWith("javascript:") || !t) {
              return text;
            }
            return `[${t}](${A.shortUrl(href)})`;
          }
          case "strong":
          case "b":
            return text.trim() ? `**${text.trim()}** ` : "";
          case "em":
          case "i":
            return text.trim() ? `*${text.trim()}* ` : "";
          case "code":
          case "kbd":
            return text.trim() ? `\`${text.trim()}\`` : "";
          case "br":
            return "\n";
          case "img": {
            const alt = A.clean(el.getAttribute("alt") || "", 100);
            return alt ? `![${alt}]` : "";
          }
          case "input":
            return A.isPassword(el) ? "" : el.value ? ` [${A.clean(el.value, 60)}] ` : "";
          default:
            return text;
        }
      };
      const block = (node, prefix = "") => {
        for (const c of A.childNodesOf(node)) {
          if (c.nodeType === Node.TEXT_NODE) {
            line += c.data.replace(/\s+/g, " ");
            continue;
          }
          if (c.nodeType !== Node.ELEMENT_NODE || skip(c)) {
            continue;
          }
          const tag = c.localName;
          const heading = tag.match(/^h([1-6])$/);
          if (heading) {
            flush();
            const t = A.clean(inline(c), 300);
            if (t) {
              blocks.push(plain ? t : `${"#".repeat(Number(heading[1]))} ${t}`);
            }
          } else if (tag === "ul" || tag === "ol") {
            flush();
            let i = 1;
            const items = [];
            for (const li of c.children) {
              if (li.localName !== "li" || skip(li)) {
                continue;
              }
              const t = A.clean(inline(li), 600);
              if (t) {
                items.push(`${prefix}${tag === "ol" ? `${i++}.` : "-"} ${t}`);
              }
            }
            if (items.length) {
              blocks.push(items.join("\n"));
            }
          } else if (tag === "pre") {
            flush();
            const t = c.textContent.replace(/\n+$/, "");
            if (t.trim()) {
              blocks.push(plain ? t : "```\n" + t + "\n```");
            }
          } else if (tag === "blockquote") {
            flush();
            const t = A.clean(inline(c), 1000);
            if (t) {
              blocks.push(plain ? t : `> ${t}`);
            }
          } else if (tag === "table") {
            flush();
            const rows = Array.from(c.querySelectorAll("tr")).slice(0, 60).map(tr =>
              Array.from(tr.children).map(td => A.clean(inline(td), 80).replace(/\|/g, "/"))
            );
            if (rows.length) {
              if (plain) {
                blocks.push(rows.map(r => r.join("\t")).join("\n"));
              } else {
                const width = Math.max(...rows.map(r => r.length));
                const fmt = r => `| ${[...r, ...Array(width - r.length).fill("")].join(" | ")} |`;
                blocks.push([fmt(rows[0]), `|${" --- |".repeat(width)}`, ...rows.slice(1).map(fmt)].join("\n"));
              }
            }
          } else if (tag === "hr") {
            flush();
            if (!plain) {
              blocks.push("---");
            }
          } else if (BLOCK.has(tag) || tag === "li" || tag === "body") {
            flush();
            block(c, prefix);
            flush();
          } else {
            line += inlineElement(c);
          }
        }
      };
      block(root);
      flush();
      return blocks.join("\n\n");
    }

    A.readPage = function (params) {
      const max = params.max_chars || 20000;
      let title = document.title;
      let byline = null;
      let text = null;
      let source = "page";
      if (!params.viewport_only && typeof Readability === "function") {
        try {
          const article = new Readability(document.cloneNode(true), { charThreshold: 400 }).parse();
          if (article && article.content && (article.textContent || "").trim().length > 200) {
            const parsed = new DOMParser().parseFromString(article.content, "text/html");
            text = toMarkdown(parsed.body, { plain: params.plain });
            title = article.title || title;
            byline = article.byline || null;
            source = "article";
          }
        } catch (e) {}
      }
      if (!text) {
        text = toMarkdown(document.body || document.documentElement, {
          live: true,
          viewportOnly: !!params.viewport_only,
          plain: params.plain,
        });
      }
      const total = text.length;
      if (total > max) {
        text = text.slice(0, max);
        text = text.slice(0, Math.max(text.lastIndexOf("\n"), max - 200));
      }
      return { title, byline, source, text, total, url: location.href };
    };

    A.getHtml = function (params) {
      const el = params.node != null || params.selector ? A.resolveTarget(params) : document.body || document.documentElement;
      const copy = el.cloneNode(true);
      const scrub = root => {
        for (const input of root.querySelectorAll ? root.querySelectorAll("input[type=password]") : []) {
          if (input.hasAttribute("value")) {
            input.setAttribute("value", "redacted");
          }
        }
        for (const s of root.querySelectorAll ? root.querySelectorAll("script, style") : []) {
          s.textContent = s.textContent.trim() ? "\u2026" : "";
        }
      };
      scrub(copy);
      if (copy.matches && copy.matches("input[type=password]") && copy.hasAttribute("value")) {
        copy.setAttribute("value", "redacted");
      }
      let html = params.outer === false ? copy.innerHTML : copy.outerHTML;
      const total = html.length;
      const max = params.max_chars || 8000;
      if (total > max) {
        html = html.slice(0, max);
      }
      return { html, total, target: params.node != null || params.selector ? A.describe(el) : "body" };
    };

    A.pageText = function () {
      return (document.body || document.documentElement).innerText || "";
    };

    A.check = function (params) {
      if (params.text) {
        return { met: A.pageText().toLowerCase().includes(params.text.toLowerCase()) };
      }
      if (params.text_gone) {
        return { met: !A.pageText().toLowerCase().includes(params.text_gone.toLowerCase()) };
      }
      if (params.node != null || params.selector) {
        let present = false;
        try {
          const el = A.resolveTarget(params);
          present = !A.isHidden(el);
        } catch (e) {
          if (e.code === "stale_document") {
            throw e;
          }
          present = false;
        }
        return { met: params.gone ? !present : present };
      }
      return { met: true };
    };

    A.textContext = function (params) {
      const text = A.pageText().replace(/\s+/g, " ");
      const needle = params.case_sensitive ? params.text : params.text.toLowerCase();
      const hay = params.case_sensitive ? text : text.toLowerCase();
      const snippets = [];
      let at = hay.indexOf(needle);
      while (at >= 0 && snippets.length < 5) {
        const start = Math.max(0, at - 70);
        const end = Math.min(text.length, at + needle.length + 70);
        snippets.push(`${start > 0 ? "\u2026" : ""}${text.slice(start, end).trim()}${end < text.length ? "\u2026" : ""}`);
        at = hay.indexOf(needle, at + needle.length);
      }
      return { snippets };
    };
  })(AgentF);
}
