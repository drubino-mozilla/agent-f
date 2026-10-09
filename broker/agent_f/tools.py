"""MCP tool definitions."""

import base64
import re
from datetime import datetime

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.utilities.types import Image

from . import envelope
from .bridge import BrowserError
from .events import quote, short_url
from . import tools_extra, tools_webdriver
from .audit import Audit, audited
from .toolkit import AgentFError, Toolkit, split_root
from .tools_webdriver import eval_fallback, read_fallback, screenshot_fallback, unreachable_fallback
from .tools_webdriver import trusted as trusted_input

LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")

SINCE_DOC = "since: The since token from the end of your previous Agent F result."
TAB_DOC = "tab: Tab id from list_tabs. Defaults to your current tab. Not needed with ref."
BROWSER_DOC = "browser: Browser label. Needed only when several browsers are connected."
TARGET_DOC = "ref: An element ref from snapshot or find, such as e57.\n    selector: A CSS selector, used when there is no ref."


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"{n} {word}"
    return f"{n} {word}{'es' if word.endswith(('ch', 'sh', 's', 'x')) else 's'}"


def register(mcp: FastMCP, kit: Toolkit, audit: Audit | None = None) -> None:
    hub = kit.hub
    tool = audited(mcp, audit)
    tools_extra.register(tool, kit)
    tools_webdriver.register(tool, kit)

    # Browsers.

    @tool()
    async def list_browsers(ctx: Context, since: str | None = None) -> str:
        """List the Firefox profiles connected to Agent F, with their labels and versions.

        Args:
            since: The since token from the end of your previous Agent F result.
        """
        session = kit.session_for(ctx, since)
        conns = hub.connected()
        if not conns:
            return kit.finish(session, "No browsers connected. Start Firefox with the Agent F add-on enabled.")
        lines = [f"{_plural(len(conns), 'browser')} connected:"]
        for conn in conns:
            started = datetime.fromtimestamp(conn.connected_at).strftime("%H:%M")
            flags = " [paused]" if conn.info.get("paused") else ""
            if conn.profile_id == session.browser:
                flags += " [your current browser]"
            lines.append(f"- {conn.label}: {conn.browser_name}, connected since {started}{flags}")
        return kit.finish(session, "\n".join(lines))

    @tool()
    async def label_browser(ctx: Context, label: str, browser: str | None = None, since: str | None = None) -> str:
        """Rename a connected browser. The label is stored in that Firefox profile.

        Args:
            label: New label: letters, digits, dot, dash or underscore, up to 40 characters.
            browser: Current label of the browser to rename. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        session = kit.session_for(ctx, since)
        try:
            if not LABEL_RE.match(label):
                raise AgentFError("bad_label", "Labels use letters, digits, '.', '-' or '_', up to 40 characters.")
            conn = kit.resolve_browser(session, browser)
            other = hub.find(label)
            if other is not None and other is not conn:
                raise AgentFError("label_taken", f"Another connected browser is already labelled {label!r}.")
            old = conn.label
            await conn.request("set_label", {"label": label}, call=session.id)
            conn.label = label
        except (AgentFError, BrowserError) as err:
            raise kit.fail(session, err)
        return kit.finish(session, f"Renamed browser {old} to {label}.", header=envelope.browser_header(label))

    @tool()
    async def reload_extension(ctx: Context, browser: str | None = None, since: str | None = None) -> str:
        """For Agent F development only: reload the Agent F add-on in one browser, picking up code changes.

        Args:
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        session = kit.session_for(ctx, since)
        try:
            conn = kit.resolve_browser(session, browser)
            await conn.request("reload_extension", {}, call=session.id)
        except (AgentFError, BrowserError) as err:
            raise kit.fail(session, err)
        return kit.finish(session, f"Reloading Agent F in {conn.label}; it reconnects in a few seconds.",
                          header=envelope.browser_header(conn.label))

    # Tabs and windows.

    @tool()
    async def list_tabs(ctx: Context, browser: str | None = None, window: int | None = None,
                        query: str | None = None, since: str | None = None) -> str:
        """List open windows and tabs, grouped by window.

        Args:
            browser: Browser label. Needed only when several browsers are connected.
            window: Only list this window.
            query: Only list tabs whose title or URL contains this text.
            since: The since token from the end of your previous Agent F result.
        """
        session = kit.session_for(ctx, since)
        try:
            conn = kit.resolve_browser(session, browser)
            result = await conn.request("list_tabs", {"window": window, "query": query}, call=session.id)
        except (AgentFError, BrowserError) as err:
            raise kit.fail(session, err)
        all_tabs = [t for w in result.get("windows", []) for t in w.get("tabs", [])]
        if window is None and not query:
            conn.tabs.replace(all_tabs)
        else:
            for t in all_tabs:
                conn.tabs.update(t)
        return kit.finish(session, _render_tab_list(result, session, conn), header=envelope.browser_header(conn.label))

    @tool()
    async def open_tab(ctx: Context, url: str | None = None, browser: str | None = None, window: int | None = None,
                       group: str = "Agent F", container: str | None = None, background: bool = True,
                       since: str | None = None) -> str:
        """Open a new tab, in the background by default, and make it your current tab.

        Args:
            url: Address to load. Omit for a blank tab.
            browser: Browser label. Needed only when several browsers are connected.
            window: Window id. Defaults to the window the user used last.
            group: Tab group to put it in. Pass an empty string for none.
            container: Name of a Firefox container (such as Work) to open it in.
            background: Open without switching to it. Keep this true unless the user wants to see the tab.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result, conn):
            tab = result.get("tab") or {}
            where = "in the background" if background else "and switched to it"
            return f"Opened tab {tab.get('id')} {where}. It is now your current tab.", None
        return await kit.run_browser(ctx, since, browser=browser, command="open_tab", render=render, timeout=40, params={
            "url": url, "window": window, "group": group, "container": container, "background": background})

    @tool()
    async def close_tab(ctx: Context, tab: int | None = None, tabs: list[int] | None = None,
                        browser: str | None = None, since: str | None = None) -> str:
        """Close one or more tabs. Ask the user before closing tabs you didn't open.

        Args:
            tab: Tab id to close.
            tabs: Several tab ids to close.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        if tab is None and not tabs:
            session = kit.session_for(ctx, since)
            raise kit.fail(session, AgentFError("bad_arguments", "Pass tab or tabs."))

        def render(result, conn):
            closed = result.get("closed", [])
            lines = [f"Closed tab {t['id']} {quote(t.get('title'))} {short_url(t.get('url'))}" for t in closed]
            for missing in result.get("missing", []):
                lines.append(f"Tab {missing} was already gone.")
            return "\n".join(lines) or "Nothing to close.", None
        return await kit.run_browser(ctx, since, browser=browser, command="close_tabs", render=render,
                                     params={"tab": tab, "tabs": tabs or []})

    @tool()
    async def open_window(ctx: Context, url: str | None = None, private: bool = False, browser: str | None = None,
                          since: str | None = None) -> str:
        """Open a new browser window. Firefox brings it to the front; prefer open_tab, which stays in the background.

        Args:
            url: Address to load.
            private: Open a private window. Works only if the user allowed Agent F in private windows.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result, conn):
            return f"Opened window {result.get('window')}.", None
        return await kit.run_browser(ctx, since, browser=browser, command="open_window", render=render, timeout=40,
                                     params={"url": url, "private": private})

    @tool()
    async def close_window(ctx: Context, window: int, browser: str | None = None, since: str | None = None) -> str:
        """Close a browser window and all its tabs. Ask the user first unless you opened it.

        Args:
            window: Window id from list_tabs.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result, conn):
            return f"Closed window {result.get('window')} ({_plural(result.get('tabs', 0), 'tab')}).", None
        return await kit.run_browser(ctx, since, browser=browser, command="close_window", render=render,
                                     params={"window": window})

    @tool()
    async def unload_tab(ctx: Context, tab: int, browser: str | None = None, since: str | None = None) -> str:
        """Unload a background tab to free memory. It stays in the tab strip and reloads when next used.

        Args:
            tab: Tab id. It can't be the tab showing in its window.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        return await kit.run(ctx, since, browser=browser, tab=tab, command="unload_tab", params={},
                             render=lambda r: "Unloaded the tab. Agent F reloads it when a tool next uses it.")

    @tool()
    async def focus_tab(ctx: Context, tab: int, browser: str | None = None, since: str | None = None) -> str:
        """Switch to a tab and bring its window to the front. Only when the user asks to see something.

        Args:
            tab: Tab id.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        return await kit.run(ctx, since, browser=browser, tab=tab, command="focus_tab", params={},
                             render=lambda r: "Switched to the tab and brought its window to the front.")

    @tool()
    async def navigate(ctx: Context, url: str | None = None, action: str | None = None, tab: int | None = None,
                       wait: str = "load", timeout: float = 20, browser: str | None = None,
                       since: str | None = None) -> str:
        """Load an address in a tab, or go back, forward or reload.

        Args:
            url: Address to load.
            action: back, forward or reload, instead of url.
            tab: Tab id. Defaults to your current tab; there is no fallback to the user's tab.
            wait: load (default), commit, or none.
            timeout: Seconds to wait for the page.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result):
            if url:
                return f"Loaded {url}."
            return {"back": "Went back.", "forward": "Went forward.", "reload": "Reloaded."}.get(action or "", "Done.")
        return await kit.run(ctx, since, browser=browser, tab=tab, command="navigate", render=render,
                             params={"url": url, "action": action, "wait": wait, "timeout": timeout},
                             timeout=timeout + 10, default_to_user_tab=False)

    # Reading.

    @tool()
    async def snapshot(ctx: Context, tab: int | None = None, mode: str = "interactive", root: str | None = None,
                       viewport_only: bool = False, max_chars: int = 12000, browser: str | None = None,
                       since: str | None = None) -> str:
        """Outline of the page as roles, names, states and refs, to decide what to act on.

        Interactive mode keeps landmarks, headings and interactive elements; full mode adds text.
        Large parts of the page are collapsed to fit max_chars; pass a collapsed node's ref as root to expand it.

        Args:
            tab: Tab id. Defaults to your current tab. Not needed with a ref root.
            mode: interactive (default) or full.
            root: A ref or CSS selector to outline only that part of the page.
            viewport_only: Only what is currently visible in the tab.
            max_chars: Size budget for the outline.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        ref, selector = split_root(root)

        def render(result):
            text = result.get("text") or "(nothing interactive here)"
            body = envelope.page_content(text)
            total, shown = result.get("total") or 0, result.get("shown") or 0
            if result.get("truncated") or total > shown:
                size = ""
                if total > shown > 0:
                    more = ", and the page has more than Agent F counted" if result.get("incomplete") else ""
                    size = f" It shows about {max(1, round(100 * shown / total))}% of the full outline{more}."
                body += f"\n(The outline was cut to fit.{size} Pass a section's ref as root, or a larger max_chars.)"
            return body
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="snapshot",
                             render=render, params={"mode": mode, "viewport_only": viewport_only, "max_chars": max_chars})

    @tool()
    async def find(ctx: Context, role: str | None = None, name: str | None = None, text: str | None = None,
                   selector: str | None = None, limit: int = 10, tab: int | None = None, browser: str | None = None,
                   since: str | None = None) -> str:
        """Find elements by role, accessible name, contained text or CSS selector, and get refs for them.

        Args:
            role: Role such as button, link, textbox, checkbox, combobox, heading.
            name: Text the element's accessible name contains (case-insensitive).
            text: Text the element contains (case-insensitive).
            selector: CSS selector.
            limit: Maximum matches to return.
            tab: Tab id. Defaults to your current tab.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result):
            total = result.get("total", 0)
            if not total:
                return "No matches."
            head = f"{_plural(total, 'match')}" + (f"; showing the first {limit}" if total > limit else "") + ":"
            return head + "\n" + envelope.page_content(result.get("text", ""))
        return await kit.run(ctx, since, browser=browser, tab=tab, command="find", render=render, params={
            "role": role, "name": name, "text": text, "selector": selector, "limit": limit})

    @tool()
    async def read_page(ctx: Context, tab: int | None = None, viewport_only: bool = False, max_chars: int = 20000,
                        plain: bool = False, browser: str | None = None, since: str | None = None) -> str:
        """The page's main content as Markdown (article text, with links), for reading and summarising.

        Args:
            tab: Tab id. Defaults to your current tab.
            viewport_only: Only what is currently visible.
            max_chars: Maximum characters to return.
            plain: Plain text instead of Markdown.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result):
            lines = [f"Title: {result.get('title') or ''}"]
            if result.get("byline"):
                lines.append(f"Byline: {result['byline']}")
            lines.append("Main article content:" if result.get("source") == "article" else "Page content:")
            body = "\n".join(lines) + "\n" + envelope.page_content(result.get("text") or "(no text)")
            total, shown = result.get("total", 0), len(result.get("text") or "")
            if total > shown:
                body += f"\n(Showing {shown} of {total} characters; pass a larger max_chars for more.)"
            return body
        return await kit.run(ctx, since, browser=browser, tab=tab, command="read_page", render=render, params={
            "viewport_only": viewport_only, "max_chars": max_chars, "plain": plain},
            perform=unreachable_fallback(kit, "read_page", read_fallback))

    @tool()
    async def find_in_page(ctx: Context, text: str, case_sensitive: bool = False, highlight: bool = False,
                           tab: int | None = None, browser: str | None = None, since: str | None = None) -> str:
        """Search the page's text with Firefox's own find, returning the match count and surrounding text.

        Args:
            text: Text to look for.
            case_sensitive: Match case.
            highlight: Highlight the matches in the page, as Find in Page does.
            tab: Tab id. Defaults to your current tab.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result):
            count = result.get("count", 0)
            body = f"{_plural(count, 'match')} for {text!r}."
            snippets = result.get("snippets") or []
            if snippets:
                body += "\n" + envelope.page_content("\n".join(f"- {s}" for s in snippets))
            return body
        return await kit.run(ctx, since, browser=browser, tab=tab, command="find_in_page", render=render, params={
            "text": text, "case_sensitive": case_sensitive, "highlight": highlight})

    @tool()
    async def screenshot(ctx: Context, tab: int | None = None, ref: str | None = None, selector: str | None = None,
                         full_page: bool = False, marks: bool = False, format: str = "jpeg", max_width: int = 1280,
                         browser: str | None = None, since: str | None = None):
        """Capture a tab, a whole page, or one element, without bringing the tab to the front.

        Args:
            tab: Tab id. Defaults to your current tab. Not needed with ref.
            ref: Capture just this element.
            selector: Capture just the first element matching this CSS selector.
            full_page: Capture the whole page rather than what's in view.
            marks: Draw numbered boxes over interactive elements in view, with a legend mapping numbers to refs.
            format: jpeg (default) or png.
            max_width: Maximum image width in pixels.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result):
            image = Image(data=base64.b64decode(result["image"]), format=result.get("mime", "image/jpeg").split("/")[-1])
            what = "the element" if (ref or selector) else "the whole page" if full_page else "what's in view"
            body = f"Screenshot of {what}."
            legend = result.get("legend") or []
            if legend:
                body += "\nMarks: " + ", ".join(f"{m['n']}={m['token']}" for m in legend)
            return body, [image]
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="screenshot",
                             render=render, timeout=40, params={
                                 "full_page": full_page, "marks": marks, "format": format, "max_width": max_width},
                             perform=unreachable_fallback(kit, "screenshot", screenshot_fallback))

    @tool()
    async def get_html(ctx: Context, tab: int | None = None, ref: str | None = None, selector: str | None = None,
                       outer: bool = True, max_chars: int = 8000, browser: str | None = None,
                       since: str | None = None) -> str:
        """HTML of one element (or the page body), for precise attributes and structure. Scripts are elided.

        Args:
            tab: Tab id. Defaults to your current tab. Not needed with ref.
            ref: An element ref from snapshot or find.
            selector: A CSS selector, used when there is no ref.
            outer: Include the element's own tag (outerHTML) rather than just its contents.
            max_chars: Maximum characters to return.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(result):
            html, total = result.get("html", ""), result.get("total", 0)
            body = f"HTML of {result.get('target')}:\n" + envelope.page_content(html)
            if total > len(html):
                body += f"\n(Showing {len(html)} of {total} characters.)"
            return body
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="get_html",
                             render=render, params={"outer": outer, "max_chars": max_chars})

    # Acting. Input is simulated; results say what happened.

    def action_timeout(settle_ms: int) -> float:
        return settle_ms / 1000 + 30

    @tool()
    async def click(ctx: Context, ref: str | None = None, selector: str | None = None, button: str = "left",
                    count: int = 1, modifiers: list[str] | None = None, x: float | None = None, y: float | None = None,
                    trusted: bool = False, settle_ms: int = 3000, tab: int | None = None, browser: str | None = None,
                    since: str | None = None) -> str:
        """Click an element. Reports what happened: navigation, new tabs, dialogs, focus.

        Args:
            ref: An element ref from snapshot or find, such as e57.
            selector: A CSS selector, used when there is no ref.
            button: left (default), middle or right.
            count: 2 for a double click.
            modifiers: Keys held down, such as ["Control"] or ["Shift"].
            x: Horizontal offset inside the element, in pixels. Defaults to its centre.
            y: Vertical offset inside the element, in pixels. Defaults to its centre.
            trusted: Send a real click through Firefox's WebDriver server, still in the background. The page sees
                a trusted click and a user gesture, for sites that ignore simulated clicks and for popups,
                clipboard and file pickers. Needs Firefox's remote control on.
            settle_ms: Longest time to wait for the page to settle afterwards.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="click",
                             render=lambda r: f"Clicked {r.get('target')}.", timeout=action_timeout(settle_ms), params={
                                 "button": button, "count": count, "modifiers": modifiers or [], "x": x, "y": y,
                                 "settle_ms": settle_ms},
                             perform=trusted_input(kit, "click") if trusted else None)

    @tool(name="type")
    async def type_text(ctx: Context, text: str, ref: str | None = None, selector: str | None = None,
                        clear: bool = True, submit: bool = False, method: str = "insert", trusted: bool = False,
                        settle_ms: int = 3000, tab: int | None = None, browser: str | None = None,
                        since: str | None = None) -> str:
        """Type text into a field or editable area.

        Args:
            text: Text to type.
            ref: An element ref from snapshot or find, such as e57.
            selector: A CSS selector, used when there is no ref.
            clear: Replace what's there (default) rather than add to it.
            submit: Press Enter afterwards, submitting the form if there is one.
            method: insert (default, like pasting) or keys (one key at a time, sending keydown and keyup
                around each character, for fields that react per keystroke: suggestions, formatting,
                send on Enter). Hidden inputs that read keystrokes, as in Google Docs, get keys automatically.
                Both send simulated key events; pages that check for real ones need trusted.
            trusted: Type with real keystrokes through Firefox's WebDriver server, still in the background, so
                the page gets trusted key events. Needs Firefox's remote control on.
            settle_ms: Longest time to wait for the page to settle afterwards.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            body = f"Typed into {r.get('target')}."
            if r.get("value") is not None:
                body += f" Its value is now {quote(r.get('value'), 120)}."
            if r.get("method") == "value":
                body += " (Firefox's editor wasn't available here, so Agent F set the value directly.)"
            if r.get("method") == "keys_auto":
                body += " (It's a hidden input that the page reads keystrokes from, as in Google Docs, so Agent F typed key by key.)"
            if r.get("hiddenInput"):
                body += " Check the result with screenshot or read_page; the page may take a moment to show it."
            if r.get("submitted"):
                body += f" Then {r['submitted']}."
            elif trusted and submit:
                body += " Then pressed Enter."
            return body
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="type",
                             render=render, timeout=action_timeout(settle_ms), params={
                                 "text": text, "clear": clear, "submit": submit, "method": method,
                                 "settle_ms": settle_ms},
                             perform=trusted_input(kit, "type") if trusted else None)

    @tool()
    async def press_key(ctx: Context, keys: str, ref: str | None = None, selector: str | None = None,
                        trusted: bool = False, settle_ms: int = 3000, tab: int | None = None,
                        browser: str | None = None, since: str | None = None) -> str:
        """Press keys, such as "Enter", "Escape", "Tab", "Control+a", or several separated by spaces.

        Args:
            keys: Keys or chords to press, separated by spaces.
            ref: Element to focus first. Defaults to whatever has focus in the page.
            selector: A CSS selector, used when there is no ref.
            trusted: Press real keys through Firefox's WebDriver server, still in the background, so the page
                gets trusted key events and the browser's own default actions. Needs Firefox's remote control on.
            settle_ms: Longest time to wait for the page to settle afterwards.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            keys_done = r.get("keys") or [f"{k}: sent" for k in keys.split()]
            return f"Pressed keys on {r.get('target')}:\n" + "\n".join(f"- {k}" for k in keys_done)
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="press_key",
                             render=render, timeout=action_timeout(settle_ms),
                             params={"keys": keys, "settle_ms": settle_ms},
                             perform=trusted_input(kit, "press_key") if trusted else None)

    @tool()
    async def hover(ctx: Context, ref: str | None = None, selector: str | None = None, trusted: bool = False,
                    settle_ms: int = 1500, tab: int | None = None, browser: str | None = None,
                    since: str | None = None) -> str:
        """Move the pointer over an element, for menus and tooltips driven by script.

        Simulated hovering doesn't apply CSS :hover styles; trusted does.

        Args:
            ref: An element ref from snapshot or find, such as e57.
            selector: A CSS selector, used when there is no ref.
            trusted: Move the real WebDriver pointer through Firefox's WebDriver server, still in the background,
                so :hover styles apply. Needs Firefox's remote control on.
            settle_ms: Longest time to wait for the page to settle afterwards.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="hover",
                             render=lambda r: f"Hovered over {r.get('target')}.", timeout=action_timeout(settle_ms),
                             params={"settle_ms": settle_ms},
                             perform=trusted_input(kit, "hover") if trusted else None)

    @tool()
    async def select_option(ctx: Context, values: list[str] | None = None, labels: list[str] | None = None,
                            ref: str | None = None, selector: str | None = None, settle_ms: int = 3000,
                            tab: int | None = None, browser: str | None = None, since: str | None = None) -> str:
        """Choose options in a native select element, by value or visible label.

        Args:
            values: Option values to select.
            labels: Option labels (visible text) to select.
            ref: The select element's ref.
            selector: A CSS selector, used when there is no ref.
            settle_ms: Longest time to wait for the page to settle afterwards.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        if not values and not labels:
            session = kit.session_for(ctx, since)
            raise kit.fail(session, AgentFError("bad_arguments", "Pass values or labels: the options to choose."))
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="select_option",
                             render=lambda r: f"Selected {', '.join(quote(s, 60) for s in r.get('selected', []))} in {r.get('target')}.",
                             timeout=action_timeout(settle_ms),
                             params={"values": values or [], "labels": labels or [], "settle_ms": settle_ms})

    @tool()
    async def scroll(ctx: Context, ref: str | None = None, selector: str | None = None, direction: str | None = None,
                     amount: int | None = None, settle_ms: int = 1500, tab: int | None = None,
                     browser: str | None = None, since: str | None = None) -> str:
        """Scroll an element into view, or scroll the page (or a scrollable element) in a direction.

        Args:
            ref: Element to scroll into view; with direction, the scrollable element to scroll.
            selector: A CSS selector, used when there is no ref.
            direction: down, up, left or right.
            amount: Pixels to scroll. Defaults to most of a screen.
            settle_ms: Longest time to wait for the page to settle afterwards (lazy loading).
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            if r.get("target") and not direction:
                return f"Scrolled {r['target']} into view."
            end = " It's at the end." if r.get("atEnd") else ""
            return f"Scrolled {direction or 'down'} {abs(r.get('moved', 0))} px, to {r.get('position')} of {r.get('max')}.{end}"
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="scroll",
                             render=render, timeout=action_timeout(settle_ms), params={
                                 "direction": direction or ("down" if not (ref or selector) else None),
                                 "amount": amount, "settle_ms": settle_ms})

    @tool()
    async def wait_for(ctx: Context, text: str | None = None, text_gone: str | None = None, ref: str | None = None,
                       selector: str | None = None, gone: bool = False, url: str | None = None, load: bool = False,
                       network_idle: bool = False, timeout: float = 10, tab: int | None = None,
                       browser: str | None = None, since: str | None = None) -> str:
        """Wait for one condition: text to appear or go, an element to appear or go, the address, load, or quiet network.

        Args:
            text: Wait until the page shows this text.
            text_gone: Wait until the page no longer shows this text.
            ref: Wait for this element (see gone).
            selector: Wait for an element matching this CSS selector (see gone).
            gone: With ref or selector, wait for it to disappear instead.
            url: Wait until the address contains this text, or matches it with * wildcards.
            load: Wait for the page to finish loading.
            network_idle: Wait until no requests have been in flight for half a second.
            timeout: Seconds to wait, up to 60.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            if r.get("met"):
                return f"Done waiting for {r.get('what')} after {r.get('waited')} s."
            return f"Gave up after {r.get('waited')} s waiting for {r.get('what')}."
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="wait_for",
                             render=render, timeout=min(timeout, 60) + 15, params={
                                 "text": text, "text_gone": text_gone, "gone": gone, "url": url, "load": load,
                                 "network_idle": network_idle, "timeout": timeout})

    # Scripts.

    @tool()
    async def eval_page(ctx: Context, code: str, frame: int = 0, timeout: float = 10, tab: int | None = None,
                        browser: str | None = None, since: str | None = None) -> str:
        """Run JavaScript in the page, even on pages with strict security policies. Returns the result as JSON.

        The code runs in the extension's view of the page: document and the DOM work as usual, and `page` is the
        page's own window, for its global variables and functions. Pass an expression, or statements: the last
        expression's value is returned, or use return. Promises are awaited. On pages extensions can't reach,
        it runs through Firefox's WebDriver server if remote control is on. Never use this to read password fields.

        Args:
            code: An expression, or statements.
            frame: Frame id, for code in an iframe. Defaults to the top frame.
            timeout: Seconds to wait for the result.
            tab: Tab id. Defaults to your current tab.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            value, total = r.get("value", ""), r.get("total", 0)
            body = "Returned:\n" + envelope.page_content(value)
            if total > len(value):
                body += f"\n(Showing {len(value)} of {total} characters.)"
            return body
        return await kit.run(ctx, since, browser=browser, tab=tab, command="eval_page", render=render,
                             timeout=timeout + 10, params={"code": code, "frame": frame, "timeout": timeout},
                             perform=unreachable_fallback(kit, "eval_page", eval_fallback))


def _render_tab_list(result: dict, session, conn) -> str:
    windows = result.get("windows", [])
    groups = {g["id"]: g for g in result.get("groups", [])}
    containers = {c["cookieStoreId"]: c["name"] for c in result.get("containers", [])}
    focused_window = result.get("focusedWindow")
    firefox_focused = result.get("firefoxFocused", False)
    tab_count = sum(len(w.get("tabs", [])) for w in windows)
    summary = f"{_plural(len(windows), 'window')}, {_plural(tab_count, 'tab')}."
    if not windows:
        return summary

    lines = []
    for w in windows:
        notes = []
        if w["id"] == focused_window:
            notes.append("focused" if firefox_focused else "last focused")
        if w.get("incognito"):
            notes.append("private")
        if w.get("state") == "minimized":
            notes.append("minimized")
        lines.append(f"Window {w['id']}" + (f" ({', '.join(notes)})" if notes else "") + ":")
        for t in w.get("tabs", []):
            marker = "*" if t.get("active") else " "
            flags = []
            group = groups.get(t.get("groupId"))
            if group:
                flags.append(f"group {quote(group.get('title') or '')}")
            container = containers.get(t.get("cookieStoreId"))
            if container:
                flags.append(f"container {quote(container)}")
            if t.get("pinned"):
                flags.append("pinned")
            if t.get("discarded"):
                flags.append("unloaded")
            if t.get("status") == "loading":
                flags.append("loading")
            if t.get("audible"):
                flags.append("playing sound")
            if session.tab_in(conn.profile_id) == t.get("id"):
                flags.append("your current tab")
            flag_text = "".join(f" [{f}]" for f in flags)
            lines.append(f"  {marker} {t.get('id')} {quote(t.get('title'))} {short_url(t.get('url'))}{flag_text}")
    return summary + " The active tab in each window is marked *.\n" + envelope.page_content("\n".join(lines))
