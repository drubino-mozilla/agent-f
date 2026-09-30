"""Dialogs, network and console capture, files, drag, and browser data (history, bookmarks, downloads)."""

import base64
import mimetypes
import secrets
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import Context

from . import envelope
from .config import data_dir
from .events import quote, short_url
from .toolkit import AgentFError, Call, Target, Toolkit

UPLOAD_CHUNK = 512 * 1024
UPLOAD_LIMIT = 50 * 1024 * 1024


def _time(ms: float | None) -> str:
    if not ms:
        return "?"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def register(tool, kit: Toolkit) -> None:
    hub = kit.hub

    # Dialogs.

    @tool()
    async def set_dialog_policy(ctx: Context, confirm: str | None = None, prompt: str | None = None,
                                once: bool = True, tab: int | None = None, browser: str | None = None,
                                since: str | None = None) -> str:
        """Choose how Agent F answers the page's confirm() and prompt() dialogs during your actions.

        By default, alerts are dismissed, confirms answered OK and prompts cancelled, and every dialog is
        reported in the action's Effects. Set this before clicking something you expect to ask, for example
        confirm="dismiss" to answer Cancel. Dialogs while the user browses are never touched.

        Args:
            confirm: accept (OK) or dismiss (Cancel).
            prompt: Text to enter, or dismiss to cancel.
            once: Apply to the next dialog only (default), rather than to all dialogs in this tab.
            tab: Tab id. Defaults to your current tab.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        if confirm not in (None, "accept", "dismiss"):
            session = kit.session_for(ctx, since)
            raise kit.fail(session, AgentFError("bad_arguments", "confirm is accept or dismiss."))

        def render(r):
            scope = "the next dialog" if once else "every dialog in this tab"
            parts = []
            if confirm:
                parts.append(f"confirm answered {'OK' if confirm == 'accept' else 'Cancel'}")
            if prompt:
                parts.append(f"prompt answered {'Cancel' if prompt == 'dismiss' else quote(prompt)}")
            return f"Set the answers for {scope}: {', '.join(parts) or 'no change'}."
        return await kit.run(ctx, since, browser=browser, tab=tab, command="set_dialog_policy", render=render,
                             params={"confirm": confirm, "prompt": prompt, "once": once})

    # Network and console.

    @tool()
    async def set_capture(ctx: Context, network: bool | None = None, console: bool | None = None,
                          bodies: bool = True, max_body: int = 512 * 1024, tab: int | None = None,
                          browser: str | None = None, since: str | None = None) -> str:
        """Turn network or console capture on or off for one tab. Off by default; it lasts until the tab closes.

        Args:
            network: Record the tab's requests, from now on.
            console: Record console messages and uncaught errors, from now on.
            bodies: Also keep response bodies of the page's script requests (XHR and fetch).
            max_body: Largest body to keep, in bytes.
            tab: Tab id. Defaults to your current tab.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            on = [name for name in ("network", "console") if r.get(name)]
            body = f"Capturing {' and '.join(on)} in this tab." if on else "Capture is off in this tab."
            if r.get("network"):
                body += " Response bodies are " + ("kept" if r.get("bodies") else "not kept") + "."
            return body
        return await kit.run(ctx, since, browser=browser, tab=tab, command="set_capture", render=render,
                             params={"network": network, "console": console, "bodies": bodies,
                                     "max_body": max_body})

    @tool()
    async def get_network(ctx: Context, url: str | None = None, method: str | None = None,
                          status: str | None = None, after: int | None = None, limit: int = 50,
                          bodies: bool = False, tab: int | None = None, browser: str | None = None,
                          since: str | None = None) -> str:
        """Requests recorded in a tab since set_capture turned network capture on.

        Args:
            url: Only requests whose address contains this text.
            method: Only this method, such as GET or POST.
            status: Only this status, such as 404, or a class such as 5xx.
            after: Only requests numbered after this, from a previous get_network.
            limit: Most recent requests to return.
            bodies: Include request and response bodies (text only; password-like fields are redacted).
            tab: Tab id. Defaults to your current tab.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            requests = r.get("requests", [])
            if not requests:
                return f"No matching requests. Pass after={r.get('lastSeq', 0)} next time to see only newer ones."
            lines = []
            for q in requests:
                state = q["error"] or ("pending" if q["pending"] else q["status"])
                took = f" {q['duration']} ms" if q.get("duration") is not None else ""
                kind = f" {q['contentType'].split(';')[0]}" if q.get("contentType") else ""
                lines.append(f"#{q['seq']} {q['method']} {state} {q['type']}{took}{kind} {short_url(q['url'], 200)}")
                if bodies:
                    if q.get("requestBody"):
                        lines.append(f"  request body: {q['requestBody'][:2000]}")
                    if q.get("responseBody"):
                        lines.append("  response body:\n    " + q["responseBody"].replace("\n", "\n    "))
            head = f"{_plural(r.get('total', 0), 'request')}" + (
                f"; showing the last {len(requests)}" if r.get("total", 0) > len(requests) else "") + ":"
            return (head + "\n" + envelope.page_content("\n".join(lines))
                    + f"\nPass after={r.get('lastSeq')} next time to see only newer requests.")
        return await kit.run(ctx, since, browser=browser, tab=tab, command="get_network", render=render,
                             params={"url": url, "method": method, "status": status, "since_seq": after,
                                     "limit": limit, "bodies": bodies})

    @tool()
    async def get_console(ctx: Context, level: str | None = None, after: int | None = None, limit: int = 100,
                          tab: int | None = None, browser: str | None = None, since: str | None = None) -> str:
        """Console messages and uncaught errors recorded in a tab since set_capture turned console capture on.

        Args:
            level: Minimum level: debug, log, info, warn or error.
            after: Only messages numbered after this, from a previous get_console.
            limit: Most recent messages to return.
            tab: Tab id. Defaults to your current tab.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def render(r):
            messages = r.get("messages", [])
            if not messages:
                return f"No matching console messages. Pass after={r.get('lastSeq', 0)} next time to see only newer ones."
            lines = [f"#{m['seq']} [{m['level']}] {datetime.fromtimestamp(m['time'] / 1000).strftime('%H:%M:%S')} "
                     f"{m['text']}" for m in messages]
            return (f"{_plural(r.get('total', 0), 'message')}:\n" + envelope.page_content("\n".join(lines))
                    + f"\nPass after={r.get('lastSeq')} next time to see only newer messages.")
        return await kit.run(ctx, since, browser=browser, tab=tab, command="get_console", render=render,
                             params={"level": level, "since_seq": after, "limit": limit})

    # Files and drag.

    def _check_paths(paths: list[str]) -> list[Path]:
        if not paths:
            raise AgentFError("no_files", "Pass paths: the files to upload.")
        home = data_dir().resolve()
        resolved = []
        total = 0
        for raw in paths:
            path = Path(raw).expanduser().resolve()
            if not path.is_file():
                raise AgentFError("no_such_file", f"There is no file at {raw}.")
            if home == path or home in path.parents:
                raise AgentFError("forbidden_file", "Agent F won't upload its own files.")
            total += path.stat().st_size
            resolved.append(path)
        if total > UPLOAD_LIMIT:
            raise AgentFError("too_large", f"The files add up to {total} bytes; the limit is {UPLOAD_LIMIT}.")
        return resolved

    @tool()
    async def upload_file(ctx: Context, paths: list[str], ref: str | None = None, selector: str | None = None,
                          settle_ms: int = 3000, tab: int | None = None, browser: str | None = None,
                          since: str | None = None) -> str:
        """Put local files into a file input, or drop them onto a drop zone, without opening a file picker.

        Args:
            paths: Full paths of the files on this computer.
            ref: The file input, a button or label for one, or a drop zone.
            selector: A CSS selector, used when there is no ref.
            settle_ms: Longest time to wait for the page to settle afterwards.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        async def prepare(target: Target, call: Call) -> dict:
            files = _check_paths(paths)
            ids = []
            for path in files:
                upload_id = secrets.token_hex(6)
                ids.append(upload_id)
                data = path.read_bytes()
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                chunks = [data[i:i + UPLOAD_CHUNK] for i in range(0, len(data), UPLOAD_CHUNK)] or [b""]
                for index, chunk in enumerate(chunks):
                    await target.conn.request("upload_chunk", {
                        "upload": upload_id, "index": index, "name": path.name, "type": mime,
                        "data": base64.b64encode(chunk).decode("ascii")}, call=call.id)
            return {"uploads": ids}

        def render(r):
            return f"Uploaded {', '.join(r.get('names', []))}: {r.get('how')} ({r.get('target')})."
        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="upload_file",
                             render=render, prepare=prepare, timeout=settle_ms / 1000 + 60,
                             params={"settle_ms": settle_ms})

    @tool()
    async def drag(ctx: Context, ref: str | None = None, selector: str | None = None, to_ref: str | None = None,
                   to_selector: str | None = None, method: str = "auto", settle_ms: int = 3000,
                   tab: int | None = None, browser: str | None = None, since: str | None = None) -> str:
        """Drag one element onto another: HTML drag and drop for draggable items, pointer moves otherwise.

        Args:
            ref: The element to drag.
            selector: A CSS selector for it, used when there is no ref.
            to_ref: Where to drop it. Must be in the same page and frame.
            to_selector: A CSS selector for the drop target, used when there is no to_ref.
            method: auto (default) or pointer, to force pointer events for sliders and sortable lists.
            settle_ms: Longest time to wait for the page to settle afterwards.
            tab: Tab id. Not needed with ref.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        async def prepare(target: Target, call: Call) -> dict:
            if to_ref:
                record = hub.refs.get(to_ref)
                if record is None:
                    raise AgentFError("unknown_ref", f"There is no ref {to_ref}; take a new snapshot.")
                if (record.browser, record.tab, record.frame) != (target.conn.profile_id, target.tab, target.frame):
                    raise AgentFError("bad_arguments", "The drop target must be in the same page and frame as the element.")
                return {"to_node": record.node, "to_doc": record.doc}
            if to_selector:
                return {"to_selector": to_selector}
            raise AgentFError("bad_arguments", "Pass to_ref or to_selector.")

        return await kit.run(ctx, since, browser=browser, tab=tab, ref=ref, selector=selector, command="drag",
                             render=lambda r: f"From {r.get('from')} to {r.get('to')}: {r.get('how')}.",
                             prepare=prepare, timeout=settle_ms / 1000 + 30,
                             params={"method": method, "settle_ms": settle_ms})

    # Browser data.

    def browser_render(fmt):
        def render(result, conn):
            return fmt(result), None
        return render

    @tool()
    async def search_history(ctx: Context, text: str = "", newer_than: str | None = None, limit: int = 25,
                             browser: str | None = None, since: str | None = None) -> str:
        """Search the browser's history by title or address.

        Args:
            text: Words to look for. Empty lists the most recent pages.
            newer_than: Only visits after this: 24h, 7d, 2w, or a date such as 2026-09-01.
            limit: Maximum results.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def fmt(r):
            items = r.get("items", [])
            if not items:
                return "No matching history."
            lines = [f"- {quote(i['title'] or '')} {short_url(i['url'], 200)} (last visit {_time(i['lastVisit'])}, "
                     f"{_plural(i.get('visits') or 0, 'visit')})" for i in items]
            return f"{_plural(len(items), 'page')}:\n" + envelope.page_content("\n".join(lines))
        return await kit.run_browser(ctx, since, browser=browser, command="search_history", render=browser_render(fmt),
                                     params={"text": text, "since": newer_than, "limit": limit})

    @tool()
    async def search_bookmarks(ctx: Context, text: str = "", limit: int = 25, browser: str | None = None,
                               since: str | None = None) -> str:
        """Search bookmarks by title or address.

        Args:
            text: Words to look for.
            limit: Maximum results.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def fmt(r):
            items = r.get("items", [])
            if not items:
                return "No matching bookmarks."
            lines = [f"- {quote(i['title'] or '')} {short_url(i['url'], 200)} (in {i['folder'] or 'bookmarks'})"
                     for i in items]
            return f"{_plural(len(items), 'bookmark')}:\n" + envelope.page_content("\n".join(lines))
        return await kit.run_browser(ctx, since, browser=browser, command="search_bookmarks",
                                     render=browser_render(fmt), params={"text": text, "limit": limit})

    @tool()
    async def add_bookmark(ctx: Context, url: str, title: str | None = None, folder: str | None = None,
                           browser: str | None = None, since: str | None = None) -> str:
        """Bookmark an address.

        Args:
            url: Address to bookmark.
            title: Bookmark title. Defaults to the address.
            folder: Name of an existing bookmark folder. Defaults to Other Bookmarks.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        return await kit.run_browser(
            ctx, since, browser=browser, command="add_bookmark",
            render=browser_render(lambda r: f"Bookmarked {quote(r.get('title'))} in {r.get('folder') or 'bookmarks'}."),
            params={"url": url, "title": title, "folder": folder})

    @tool()
    async def list_downloads(ctx: Context, newer_than: str | None = None, state: str | None = None, limit: int = 20,
                             browser: str | None = None, since: str | None = None) -> str:
        """Recent downloads, newest first.

        Args:
            newer_than: Only downloads started after this: 24h, 7d, 2w, or a date.
            state: in_progress, complete or interrupted.
            limit: Maximum results.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def fmt(r):
            items = r.get("items", [])
            if not items:
                return "No matching downloads."
            lines = []
            for d in items:
                started = datetime.fromisoformat(d["started"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M") \
                    if d.get("started") else "?"
                size = f"{d.get('bytes', 0)} of {d['total']} bytes" if d.get("total", -1) > 0 else f"{d.get('bytes', 0)} bytes"
                extra = f", {d['error']}" if d.get("error") else ("" if d.get("exists", True) else ", file deleted")
                lines.append(f"- {d['filename']} ({d['state']}{extra}, {size}, started {started}) from {short_url(d['url'], 150)}")
            return f"{_plural(len(items), 'download')}:\n" + envelope.page_content("\n".join(lines))
        return await kit.run_browser(ctx, since, browser=browser, command="list_downloads", render=browser_render(fmt),
                                     params={"since": newer_than, "state": state, "limit": limit})

    @tool()
    async def recently_closed(ctx: Context, limit: int = 10, browser: str | None = None,
                              since: str | None = None) -> str:
        """Recently closed tabs and windows, with the session ids restore_closed needs.

        Args:
            limit: Maximum results, up to 25.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        def fmt(r):
            items = r.get("items", [])
            if not items:
                return "Nothing was closed recently."
            lines = []
            for i in items:
                when = _time(i.get("closed"))
                if i["kind"] == "tab":
                    lines.append(f"- tab {quote(i.get('title'))} {short_url(i.get('url'), 150)}, closed {when} "
                                 f"[session_id={i['sessionId']}]")
                else:
                    lines.append(f"- window with {_plural(i.get('tabs', 0), 'tab')}, first {quote(i.get('title'))}, "
                                 f"closed {when} [session_id={i['sessionId']}]")
            return envelope.page_content("\n".join(lines))
        return await kit.run_browser(ctx, since, browser=browser, command="recently_closed", render=browser_render(fmt),
                                     params={"limit": limit})

    @tool()
    async def restore_closed(ctx: Context, session_id: str, browser: str | None = None,
                             since: str | None = None) -> str:
        """Reopen a recently closed tab or window. It becomes your current tab.

        Args:
            session_id: From recently_closed.
            browser: Browser label. Needed only when several browsers are connected.
            since: The since token from the end of your previous Agent F result.
        """
        return await kit.run_browser(
            ctx, since, browser=browser, command="restore_closed",
            render=browser_render(lambda r: f"Restored the closed {r.get('kind', 'tab')}."),
            params={"session_id": session_id})
