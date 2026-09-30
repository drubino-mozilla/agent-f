"""Connections from native-messaging helpers, one per running Firefox profile."""

import asyncio
import itertools
import json
import logging
import secrets
import struct
import time

from .events import EventLog, TabCache
from .refs import Refs
from .sessions import Sessions

log = logging.getLogger(__name__)

MAX_FRAME = 256 * 1024 * 1024
AUTH_TIMEOUT = 5.0
HELLO_TIMEOUT = 30.0
REQUEST_TIMEOUT = 30.0
SETTLE_WINDOW = 1.5
# Commands that change the browser without necessarily targeting one tab. These, and any command
# aimed at a tab, claim the events that happen while they run; events around list_tabs are outside changes.
CHANGING_COMMANDS = {
    "open_tab", "close_tabs", "open_window", "close_window", "focus_tab", "navigate", "click", "hover",
    "type", "press_key", "select_option", "scroll", "eval_page", "drag", "upload_file", "restore_closed",
    "unload_tab",
}


class BrowserError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


def encode_frame(obj: dict) -> bytes:
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(data)) + data


async def read_frame(reader: asyncio.StreamReader) -> dict:
    (length,) = struct.unpack(">I", await reader.readexactly(4))
    if length > MAX_FRAME:
        raise ValueError(f"frame too large: {length}")
    return json.loads(await reader.readexactly(length))


class BrowserConnection:
    def __init__(self, hub: "Hub", reader, writer, hello: dict):
        self.hub = hub
        self.reader = reader
        self.writer = writer
        self.profile_id: str = hello.get("profileId") or secrets.token_hex(8)
        self.label: str = hello.get("label") or f"browser-{self.profile_id[:4]}"
        self.info: dict = hello
        self.connected_at = time.time()
        self.tabs = TabCache()
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        # tab id -> (call id, time until which events on that tab count as caused by that call)
        self._inflight: dict[int, tuple[str, float]] = {}
        # The same, for commands that don't target a tab (open_tab, close_tabs, ...).
        self._browser_call: tuple[str, float] | None = None

    @property
    def browser_name(self) -> str:
        return self.info.get("browser") or "Firefox"

    def _mark(self, call: str | None, tab, until: float) -> None:
        if not call:
            return
        if isinstance(tab, int):
            self._inflight[tab] = (call, until)
        else:
            self._browser_call = (call, until)
        # Tabs this call created follow the call's own timing.
        for other, (owner, _) in list(self._inflight.items()):
            if owner == call and other != tab:
                self._inflight[other] = (call, until)

    async def request(self, command: str, params: dict | None = None, call: str | None = None,
                      timeout: float = REQUEST_TIMEOUT):
        """Send a command. call identifies the tool call, so the events it causes are attributed to it."""
        params = params or {}
        request_id = f"r{next(self._ids)}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        tab = params.get("tab")
        # Any command aimed at a tab can change it (reading an unloaded tab reloads it), so it claims
        # that tab's events; wait_for doesn't, because the changes it waits for are the news.
        claims = command in CHANGING_COMMANDS or (isinstance(tab, int) and command != "wait_for")
        if not claims:
            call = None
        self._mark(call, tab, float("inf"))
        try:
            self.writer.write(encode_frame({
                "id": request_id, "type": "request", "command": command, "params": params, "call": call,
            }))
            await self.writer.drain()
            message = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise BrowserError("timeout", f"Firefox did not answer {command} within {timeout:.0f} s.")
        except (ConnectionError, OSError):
            raise BrowserError("disconnected", f"Browser {self.label} disconnected.")
        finally:
            self._pending.pop(request_id, None)
            self._mark(call, tab, time.monotonic() + SETTLE_WINDOW)
        if "error" in message:
            err = message["error"] or {}
            raise BrowserError(err.get("code", "error"), err.get("message", "Unknown error"), err.get("data"))
        return message.get("result")

    def _cause_for(self, kind: str, tab: int | None, info: dict) -> str | None:
        now = time.monotonic()
        for candidate in (tab, info.get("openerTabId")):
            if candidate is None:
                continue
            entry = self._inflight.get(candidate)
            if entry and entry[1] >= now:
                return entry[0]
        live = [e for e in self._inflight.values() if e[1] >= now]
        if self._browser_call and self._browser_call[1] >= now:
            live.append(self._browser_call)
        if kind.startswith("group_") and live:
            # A group has no single tab; credit the call that is running, or finished last.
            return max(live, key=lambda e: e[1])[0]
        if kind in ("tab_created", "window_created", "tab_removed", "window_removed", "window_focused") and \
                self._browser_call and self._browser_call[1] >= now:
            return self._browser_call[0]
        return None

    def handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "response":
            future = self._pending.get(message.get("id"))
            if future and not future.done():
                future.set_result(message)
        elif kind == "event":
            self._on_event(message)
        elif kind == "hello":
            self.info = message
            if message.get("label"):
                self.label = self.hub.unique_label(message["label"], self)

    def _on_event(self, message: dict) -> None:
        kind = message.get("event", "")
        tab = message.get("tab")
        window = message.get("window")
        info = message.get("info") or {}
        data = {**info, **(message.get("data") or {})}
        cause = self._cause_for(kind, tab, info)
        if cause and kind == "tab_created" and tab is not None:
            # The new tab's own loading events belong to the same call, for as long as it runs.
            entries = [e for e in self._inflight.values() if e[0] == cause]
            if self._browser_call and self._browser_call[0] == cause:
                entries.append(self._browser_call)
            until = max((e[1] for e in entries), default=time.monotonic() + SETTLE_WINDOW)
            self._inflight[tab] = (cause, until)

        if info:
            self.tabs.update(info)
        if kind == "tab_removed" and tab is not None:
            last = self.tabs.remove(tab)
            data.setdefault("title", last.get("title"))
            data.setdefault("url", last.get("url"))
        elif kind == "window_removed" and window is not None:
            data["tabs"] = self.tabs.remove_window(window)
        elif kind == "tab_activated" and tab is not None:
            data.setdefault("title", self.tabs.get(tab).get("title"))

        self.hub.events.add(browser=self.profile_id, label=self.label, kind=kind, tab=tab,
                            window=window, data=data, cause=cause)

    def fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_result({"error": {"code": "disconnected", "message": f"Browser {self.label} disconnected."}})

    def close(self) -> None:
        try:
            self.writer.close()
        except Exception:
            pass


class Hub:
    """Shared broker state: connected browsers, the event log and agent sessions."""

    def __init__(self, token: str):
        self.token = token
        # Identifies this broker run inside since tokens, so tokens from before a restart are recognised.
        self.instance = secrets.token_hex(2)
        self.events = EventLog()
        self.sessions = Sessions(self.events)
        self.refs = Refs()
        self.browsers: dict[str, BrowserConnection] = {}
        self._server: asyncio.base_events.Server | None = None

    def connected(self) -> list[BrowserConnection]:
        return sorted(self.browsers.values(), key=lambda c: c.connected_at)

    def find(self, label: str) -> BrowserConnection | None:
        wanted = label.casefold()
        for conn in self.browsers.values():
            if conn.label.casefold() == wanted:
                return conn
        return None

    def unique_label(self, label: str, conn: BrowserConnection) -> str:
        taken = {c.label.casefold() for c in self.browsers.values() if c is not conn}
        candidate, n = label, 2
        while candidate.casefold() in taken:
            candidate = f"{label}-{n}"
            n += 1
        return candidate

    def _register(self, conn: BrowserConnection) -> None:
        old = self.browsers.get(conn.profile_id)
        if old is not None and old is not conn:
            old.fail_pending()
            old.close()
        self.browsers[conn.profile_id] = conn
        conn.label = self.unique_label(conn.label, conn)
        self.events.add(browser=conn.profile_id, label=conn.label, kind="browser_connected")
        log.info("browser connected: %s (%s)", conn.label, conn.browser_name)

    def _unregister(self, conn: BrowserConnection) -> None:
        conn.fail_pending()
        if self.browsers.get(conn.profile_id) is conn:
            del self.browsers[conn.profile_id]
            self.events.add(browser=conn.profile_id, label=conn.label, kind="browser_disconnected")
            log.info("browser disconnected: %s", conn.label)

    async def start_bridge(self, host: str, port: int) -> int:
        self._server = await asyncio.start_server(self._handle, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def stop_bridge(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for conn in list(self.browsers.values()):
            conn.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = None
        try:
            auth = await asyncio.wait_for(read_frame(reader), AUTH_TIMEOUT)
            if auth.get("type") != "auth" or not secrets.compare_digest(str(auth.get("token", "")), self.token):
                log.warning("rejected helper connection: bad token")
                return
            hello = await asyncio.wait_for(read_frame(reader), HELLO_TIMEOUT)
            if hello.get("type") != "hello":
                log.warning("rejected helper connection: expected hello, got %s", hello.get("type"))
                return
            conn = BrowserConnection(self, reader, writer, hello)
            self._register(conn)
            while True:
                conn.handle(await read_frame(reader))
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("helper connection failed")
        finally:
            if conn is not None:
                self._unregister(conn)
            try:
                writer.close()
            except Exception:
                pass
