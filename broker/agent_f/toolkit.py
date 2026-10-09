"""The common path for tools: sessions, since tokens, choosing a browser and tab, envelopes, errors."""

import secrets
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from . import envelope
from .bridge import BrowserConnection, BrowserError, Hub
from .events import render_changes
from .refs import looks_like_ref
from .sessions import Session
from .webdriver import UNREACHABLE_HINT, WebDriverError


class AgentFError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Target:
    conn: BrowserConnection
    tab: int
    frame: int = 0
    doc: str | None = None
    node: int | None = None
    note: str | None = None

    def params(self, selector: str | None = None) -> dict:
        p = {"tab": self.tab, "frame": self.frame}
        if self.node is not None:
            p["node"] = self.node
            p["doc"] = self.doc
        elif selector:
            p["selector"] = selector
        return p


class Call:
    """One tool call. Its id attributes the events it causes; defaults live on the shared session.

    Cursor gives all chats one MCP session, so anything specific to this call (its since token,
    its id) must not be stored on the session.
    """

    def __init__(self, session: Session, since: str | None):
        self.session = session
        self.since = since
        self.id = secrets.token_hex(4)

    @property
    def browser(self) -> str | None:
        return self.session.browser

    @browser.setter
    def browser(self, value: str | None) -> None:
        self.session.browser = value

    @property
    def tab(self) -> int | None:
        return self.session.tab

    def tab_in(self, browser: str) -> int | None:
        return self.session.tab_in(browser)

    @property
    def touched(self) -> set[tuple[str, int]]:
        return self.session.touched

    def touch(self, browser: str, tab: int) -> None:
        self.session.touch(browser, tab)


def _session_id(ctx: Context) -> str:
    try:
        request = ctx.request_context.request
        if request is not None:
            sid = request.headers.get("mcp-session-id")
            if sid:
                return sid
    except (AttributeError, LookupError, ValueError):
        pass
    return "default"


class Toolkit:
    def __init__(self, hub: Hub):
        self.hub = hub

    # Sessions and envelopes.

    def session_for(self, ctx: Context, since: str | None) -> Call:
        session = self.hub.sessions.get(_session_id(ctx))
        session.last_call = time.time()
        return Call(session, since)

    def _start_of_changes(self, session: Call) -> tuple[int, str | None]:
        token = session.since
        if not token:
            return session.session.cursor, None
        instance, _, seq = token.partition("-")
        if instance != self.hub.instance or not seq.isdigit():
            return self.hub.events.seq, "Agent F restarted since your last call, so earlier changes are unknown."
        start = int(seq)
        if start < self.hub.events.oldest_seq - 1:
            return start, "Some earlier changes were too old to keep."
        return min(start, self.hub.events.seq), None

    def finish(self, session: Call, body: str, header: str | None = None,
               effects: list[str] | None = None) -> str:
        start, note = self._start_of_changes(session)
        changes = render_changes(self.hub.events.since(start), session.touched,
                                 show_label=len(self.hub.browsers) > 1, exclude=session.id)
        if note:
            changes.insert(0, note)
        session.session.cursor = self.hub.events.seq
        text = envelope.render(body, header=header, changes=changes, effects=effects)
        return f"{text}\n[since={self.hub.instance}-{self.hub.events.seq}]"

    def fail(self, session: Call, err: Exception, header: str | None = None, extra: str = "") -> ToolError:
        code = getattr(err, "code", "error")
        message = getattr(err, "message", str(err))
        body = f"Error ({code}): {message}" + (f"\n{extra}" if extra else "")
        return ToolError(self.finish(session, body, header=header))

    # Choosing a browser and tab.

    def connected_labels(self) -> str:
        return ", ".join(c.label for c in self.hub.connected()) or "none"

    def resolve_browser(self, session: Call, label: str | None) -> BrowserConnection:
        conns = self.hub.connected()
        if not conns:
            raise AgentFError("no_browser", "No Firefox is connected. Check that Firefox is running and the Agent F add-on is enabled.")
        if label:
            conn = self.hub.find(label)
            if conn is None:
                raise AgentFError("no_such_browser", f"No connected browser is labelled {label!r}. Connected: {self.connected_labels()}.")
        elif session.browser in self.hub.browsers:
            conn = self.hub.browsers[session.browser]
        elif len(conns) == 1:
            conn = conns[0]
        else:
            raise AgentFError("browser_ambiguous", f"Several browsers are connected: {self.connected_labels()}. Pass browser.")
        session.browser = conn.profile_id
        return conn

    async def resolve_target(self, session: Call, browser: str | None, tab: int | None,
                             ref: str | None = None, default_to_user_tab: bool = True) -> Target:
        if ref:
            record = self.hub.refs.get(ref)
            if record is None:
                raise AgentFError("unknown_ref", f"There is no ref {ref}. Refs come from snapshot and find; Agent F may have restarted since, so take a new snapshot.")
            conn = self.hub.browsers.get(record.browser)
            if conn is None:
                raise AgentFError("no_browser", f"The browser that ref {ref} came from is no longer connected.")
            if tab is not None and tab != record.tab:
                raise AgentFError("ref_tab_mismatch", f"Ref {ref} belongs to tab {record.tab}, not tab {tab}.")
            session.browser = conn.profile_id
            return Target(conn, record.tab, record.frame, record.doc, record.node)
        conn = self.resolve_browser(session, browser)
        if tab is not None:
            return Target(conn, tab)
        current = session.tab_in(conn.profile_id)
        if current is not None:
            return Target(conn, current, note="your current tab")
        if not default_to_user_tab:
            raise AgentFError("no_tab", "Pass tab. To work somewhere new, use open_tab first.")
        result = await conn.request("resolve_tab", {}, call=session.id)
        return Target(conn, result["tab"]["id"], note="no current tab yet, using the tab you have open")

    def header_for(self, conn: BrowserConnection, tab: dict, note: str | None) -> str:
        if not note and tab.get("userTab"):
            note = "the tab you have open"
        return envelope.tab_header(conn.label, tab, note)

    # Calling the browser.

    async def call(self, session: Call, target: Target, command: str, params: dict,
                   timeout: float = 30) -> tuple[dict, str, list[str]]:
        conn = target.conn
        result = await conn.request(command, params, call=session.id, timeout=timeout)
        result = self.hub.refs.translate_obj(conn.profile_id, target.tab, result or {})
        tab = result.pop("tab", None) or conn.tabs.get(target.tab) or {"id": target.tab}
        if tab.get("id") is not None:
            conn.tabs.update({k: v for k, v in tab.items() if k != "userTab"})
            session.touch(conn.profile_id, tab["id"])
        effects = result.pop("effects", None) or []
        return result, self.header_for(conn, tab, target.note), effects

    async def run(self, ctx: Context, since: str | None, *, browser: str | None, tab: int | None,
                  command: str, params: dict, render: Callable[[dict], str | tuple[str, list]],
                  ref: str | None = None, selector: str | None = None, timeout: float = 30,
                  default_to_user_tab: bool = True,
                  prepare: Callable[[Target, Call], Awaitable[dict]] | None = None,
                  perform: Callable[[Call, Target, dict], Awaitable[tuple[dict, str, list[str]]]] | None = None):
        """Resolve the target, call the browser, and render the result, including stale-ref recovery.

        prepare, if given, runs after the target is known and returns extra command parameters.
        perform, if given, replaces the single browser command: it gets the call, the target and the
        full parameters, and returns what call() does.
        """
        session = self.session_for(ctx, since)
        target = None
        header = None
        try:
            target = await self.resolve_target(session, browser, tab, ref, default_to_user_tab)
            cached = target.conn.tabs.get(target.tab)
            header = self.header_for(target.conn, {"id": target.tab, **cached}, target.note)
            extra = await prepare(target, session) if prepare else {}
            full = {**params, **extra, **target.params(selector)}
            if perform:
                result, header, effects = await perform(session, target, full)
            else:
                result, header, effects = await self.call(session, target, command, full, timeout)
            rendered = render(result)
            extras = []
            if isinstance(rendered, tuple):
                rendered, extras = rendered
            text = self.finish(session, rendered, header=header, effects=effects)
            return [text, *extras] if extras else text
        except (AgentFError, WebDriverError) as err:
            raise self.fail(session, err, header)
        except BrowserError as err:
            if err.code == "unreachable_page" and UNREACHABLE_HINT not in err.message:
                err.message += UNREACHABLE_HINT
            if target is not None:
                err.message = self.hub.refs.translate(target.conn.profile_id, target.tab, err.message)
            if target is not None and err.code in ("stale_document", "node_gone"):
                raise await self._stale(session, target, err, header)
            raise self.fail(session, err, header)

    async def _stale(self, session: Call, target: Target, err: BrowserError, header: str | None) -> ToolError:
        conn = target.conn
        if err.code == "node_gone":
            candidates = self.hub.refs.translate_obj(conn.profile_id, target.tab, err.data.get("candidates") or [])
            extra = ("Possible matches on the page now:\n" + "\n".join(candidates)) if candidates else \
                "Take a new snapshot to find it again."
            return self.fail(session, AgentFError("stale_ref", err.message), header, extra)
        try:
            fresh = await conn.request("snapshot", {"tab": target.tab, "mode": "interactive", "max_chars": 3000},
                                       call=session.id)
            fresh = self.hub.refs.translate_obj(conn.profile_id, target.tab, fresh)
            tab = fresh.get("tab") or {}
            extra = (f"The tab is now at {tab.get('url')}. A fresh snapshot, with new refs:\n"
                     + envelope.page_content(fresh.get("text") or "(nothing interactive)"))
        except BrowserError:
            extra = "Take a new snapshot."
        return self.fail(session, AgentFError("stale_ref", err.message), header, extra)

    async def run_browser(self, ctx: Context, since: str | None, *, browser: str | None, command: str,
                          params: dict, render: Callable[[dict, BrowserConnection], tuple[str, dict | None]],
                          timeout: float = 30):
        """For tools that act on a browser rather than an existing tab (open_tab, open_window, ...)."""
        session = self.session_for(ctx, since)
        header = None
        try:
            conn = self.resolve_browser(session, browser)
            header = envelope.browser_header(conn.label)
            result = await conn.request(command, params, call=session.id, timeout=timeout)
            result = result or {}
            effects = result.pop("effects", None) or []
            tab = result.get("tab")
            if tab and tab.get("id") is not None:
                conn.tabs.update({k: v for k, v in tab.items() if k != "userTab"})
                session.touch(conn.profile_id, tab["id"])
                header = self.header_for(conn, tab, None)
            body, _ = render(result, conn)
            return self.finish(session, body, header=header, effects=effects)
        except (AgentFError, BrowserError, WebDriverError) as err:
            raise self.fail(session, err, header)


def split_root(root: str | None) -> tuple[str | None, str | None]:
    """A snapshot root may be a ref or a CSS selector."""
    if not root:
        return None, None
    return (root.strip(), None) if looks_like_ref(root) else (None, root)
