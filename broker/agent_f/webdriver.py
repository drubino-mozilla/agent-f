"""A small WebDriver BiDi client for Firefox's own remote agent.

Agent F uses it, when the user has turned on remote control, for what an extension can't do: trusted
input, the browser's own UI and preferences, and pages extensions are kept out of. Firefox allows one
WebDriver session at a time, so each use opens a session and ends it straight away.
"""

import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 9222
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
REMOTE_CONTROL_HELP = (
    "This needs Firefox's remote control, which is off. In Firefox Nightly, set "
    "remote.experimental.dynamicstart.enabled to true in about:config, then click the remote-control "
    "button in the toolbar and choose Turn on. It stays on until Firefox quits.")
UNREACHABLE_HINT = (" With Firefox's remote control on, Agent F can still work here through WebDriver: read_page, "
                    "screenshot and eval_page work as usual, and click, hover, type and press_key work with a CSS "
                    "selector and trusted=true.")


class WebDriverError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class WebSocket:
    """The client half of RFC 6455, enough for WebDriver BiDi: text messages, ping and close."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    @classmethod
    async def connect(cls, host: str, port: int, path: str, timeout: float = 5) -> "WebSocket":
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        await writer.drain()
        head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)).decode("latin-1")
        status, *lines = head.split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines if line)}
        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if " 101 " not in status + " " or headers.get("sec-websocket-accept") != expected:
            writer.close()
            raise WebDriverError("connect_failed", f"Firefox's remote agent refused the connection ({status}).")
        return cls(reader, writer)

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 1 << 16:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.writer.write(header + mask + masked)
        await self.writer.drain()

    async def send(self, text: str) -> None:
        await self._send_frame(0x1, text.encode())

    async def recv(self) -> str:
        parts: list[bytes] = []
        while True:
            b1, b2 = await self.reader.readexactly(2)
            opcode, length = b1 & 0x0F, b2 & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", await self.reader.readexactly(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", await self.reader.readexactly(8))
            mask = await self.reader.readexactly(4) if b2 & 0x80 else None
            payload = await self.reader.readexactly(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("Firefox closed the WebDriver connection.")
            if opcode == 0x9:
                await self._send_frame(0xA, payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                parts.append(payload)
                if b1 & 0x80:
                    return b"".join(parts).decode()

    async def close(self) -> None:
        try:
            await self._send_frame(0x8, struct.pack("!H", 1000))
        except (ConnectionError, OSError):
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    profile: str | None = None


def profile_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return [appdata / "Mozilla" / "Firefox" / "Profiles"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Firefox" / "Profiles"]
    return [home / ".mozilla" / "firefox", home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
            home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox"]


def find_endpoints(roots: list[Path] | None = None) -> list[Endpoint]:
    """Firefox writes WebDriverBiDiServer.json into the profile while its WebDriver BiDi server runs."""
    found: dict[tuple[str, int], Endpoint] = {}
    for root in roots if roots is not None else profile_roots():
        try:
            files = list(root.glob("*/WebDriverBiDiServer.json"))
        except OSError:
            continue
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                host, port = str(data.get("ws_host") or "127.0.0.1"), int(data["ws_port"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if host in ("localhost", "::1"):
                host = "127.0.0.1"
            found[(host, port)] = Endpoint(host, port, str(path.parent))
    if not found:
        found[("127.0.0.1", DEFAULT_PORT)] = Endpoint("127.0.0.1", DEFAULT_PORT)
    return list(found.values())


class Session:
    """One WebDriver BiDi session. Use as `async with await Session.open(endpoint) as s`."""

    def __init__(self, ws: WebSocket, endpoint: Endpoint):
        self.ws = ws
        self.endpoint = endpoint
        self.capabilities: dict = {}
        self.events: list[dict] = []
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, endpoint: Endpoint, timeout: float = 5) -> "Session":
        try:
            ws = await WebSocket.connect(endpoint.host, endpoint.port, "/session", timeout)
        except (OSError, asyncio.TimeoutError) as err:
            raise WebDriverError("remote_control_off", REMOTE_CONTROL_HELP) from err
        session = cls(ws, endpoint)
        try:
            result = await session.command("session.new", {"capabilities": {"alwaysMatch": {
                "unhandledPromptBehavior": {"default": "ignore"}}}}, timeout=60)
        except WebDriverError as err:
            await session._close()
            if err.code == "session not created" and "active session" in err.message:
                raise WebDriverError("webdriver_busy", (
                    "Another WebDriver client, such as firefox-devtools-mcp, is connected to this Firefox, and "
                    "Firefox allows only one at a time. Try again once it disconnects.")) from err
            if "denied" in err.message:
                raise WebDriverError("webdriver_denied", "The user declined Firefox's remote-control connection prompt.") from err
            raise
        session.capabilities = result.get("capabilities") or {}
        return session

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.end()

    async def _read(self) -> None:
        try:
            while True:
                message = json.loads(await self.ws.recv())
                future = self._pending.pop(message.get("id"), None) if "id" in message else None
                if future is not None and not future.done():
                    future.set_result(message)
                elif message.get("type") == "event":
                    self.events.append(message)
        except (ConnectionError, asyncio.IncompleteReadError, OSError, ValueError) as err:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(WebDriverError("disconnected", f"The WebDriver connection closed ({err})."))
            self._pending.clear()

    async def command(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self._next += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[self._next] = future
        await self.ws.send(json.dumps({"id": self._next, "method": method, "params": params or {}}))
        try:
            message = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as err:
            self._pending.pop(self._next, None)
            raise WebDriverError("timeout", f"Firefox didn't answer {method} within {timeout:g} s.") from err
        if message.get("type") == "error" or "error" in message and "result" not in message:
            raise WebDriverError(message.get("error", "error"), message.get("message", ""))
        return message.get("result") or {}

    async def end(self) -> None:
        try:
            await self.command("session.end", timeout=5)
        except WebDriverError:
            pass
        await self._close()

    async def _close(self) -> None:
        self._reader.cancel()
        await self.ws.close()

    # Helpers.

    async def top_level_contexts(self, chrome: bool = False) -> list[dict]:
        params: dict = {"maxDepth": 0}
        if chrome:
            params["moz:scope"] = "chrome"
        return (await self.command("browsingContext.getTree", params)).get("contexts") or []

    async def evaluate(self, context: str, expression: str, timeout: float = 30, sandbox: str | None = None):
        """Evaluate in a context and return the plain value. Page exceptions raise script_error."""
        target: dict = {"context": context}
        if sandbox:
            target["sandbox"] = sandbox
        result = await self.command("script.evaluate", {
            "expression": expression, "target": target, "awaitPromise": True, "resultOwnership": "none",
        }, timeout=timeout)
        if result.get("type") == "exception":
            details = result.get("exceptionDetails") or {}
            raise WebDriverError("script_error", details.get("text") or "The script threw an exception.")
        return plain(result.get("result") or {})


async def open_session(identity: dict | None = None) -> tuple[Session, str | None]:
    """Open a session on the Firefox that shows the document identity describes ({url, timeOrigin}).

    Returns the session and the document's context. Without an identity, the only endpoint is used.
    """
    endpoints = find_endpoints()
    failures: list[WebDriverError] = []
    for endpoint in endpoints:
        try:
            session = await Session.open(endpoint)
        except WebDriverError as err:
            failures.append(err)
            continue
        if identity is None:
            if len(endpoints) == 1:
                return session, None
        else:
            try:
                context = await session.find_document(identity.get("url"), identity.get("timeOrigin"))
            except WebDriverError:
                context = None
            if context:
                return session, context
        await session.end()
    for code in ("webdriver_busy", "webdriver_denied", "remote_control_off"):
        for err in failures:
            if err.code == code:
                raise err
    if identity is None:
        raise WebDriverError("browser_ambiguous", "Several Firefox remote agents are running; Agent F can't tell which is this browser.")
    raise WebDriverError("no_such_context", "Firefox's remote agent doesn't show this tab. Is remote control on in this Firefox?")


async def _find_document(self: Session, url: str | None, time_origin: float | None) -> str | None:
    if not url:
        return None
    candidates = [c for c in await self.top_level_contexts() if c.get("url") == url]
    if time_origin is None:
        return candidates[0]["context"] if len(candidates) == 1 else None
    for c in candidates:
        try:
            origin = await self.evaluate(c["context"], "performance.timeOrigin", timeout=5)
        except WebDriverError:
            continue
        if isinstance(origin, (int, float)) and abs(origin - time_origin) < 1:
            return c["context"]
    return None


Session.find_document = _find_document


# Input. WebDriver names keys by code points in the Unicode private use area.
KEY_CODES = {
    "enter": "\ue007", "return": "\ue007", "tab": "\ue004", "escape": "\ue00c", "esc": "\ue00c",
    "backspace": "\ue003", "delete": "\ue017", "insert": "\ue016", "space": " ", "home": "\ue011",
    "end": "\ue010", "pageup": "\ue00e", "pagedown": "\ue00f", "arrowleft": "\ue012", "left": "\ue012",
    "arrowup": "\ue013", "up": "\ue013", "arrowright": "\ue014", "right": "\ue014", "arrowdown": "\ue015",
    "down": "\ue015", **{f"f{n}": chr(0xE030 + n) for n in range(1, 13)},
}
MODIFIER_CODES = {"control": "\ue009", "ctrl": "\ue009", "shift": "\ue008", "alt": "\ue00a", "option": "\ue00a",
                  "meta": "\ue03d", "cmd": "\ue03d", "command": "\ue03d"}
BUTTONS = {"left": 0, "middle": 1, "right": 2}


def key_value(name: str) -> str:
    known = KEY_CODES.get(name.lower())
    if known:
        return known
    if len(name) == 1:
        return name
    raise WebDriverError("bad_key", f"Unknown key {name}.")


def parse_chord(chord: str) -> tuple[list[str], str]:
    parts = [p.strip() for p in chord.split("+") if p.strip()]
    if chord.endswith("++"):
        parts.append("+")
    if not parts:
        raise WebDriverError("bad_key", f"Unknown key {chord!r}.")
    *mods, key = parts
    codes = []
    for m in mods:
        code = MODIFIER_CODES.get(m.lower())
        if code is None:
            raise WebDriverError("bad_key", f"Unknown modifier {m}.")
        codes.append(code)
    return codes, key_value(key)


def _with_modifiers(mods: list[str], pointer_actions: list[dict]) -> list[dict]:
    """Holds modifier keys down around pointer actions; each source pauses while the other acts."""
    pause = {"type": "pause", "duration": 0}
    pointer = {"type": "pointer", "id": "agent-f-mouse", "parameters": {"pointerType": "mouse"},
               "actions": [pause] * len(mods) + pointer_actions + [pause] * len(mods)}
    if not mods:
        return [pointer]
    keys = {"type": "key", "id": "agent-f-keys", "actions": [{"type": "keyDown", "value": m} for m in mods]
            + [pause] * len(pointer_actions) + [{"type": "keyUp", "value": m} for m in reversed(mods)]}
    return [keys, pointer]


def click_actions(x: float, y: float, button: str = "left", count: int = 1, modifiers: list[str] | None = None) -> list[dict]:
    b = BUTTONS.get(button, 0)
    steps = [{"type": "pointerMove", "x": round(x), "y": round(y), "origin": "viewport"}]
    for _ in range(max(1, min(3, count))):
        steps += [{"type": "pointerDown", "button": b}, {"type": "pointerUp", "button": b}]
    mods = [MODIFIER_CODES[m.lower()] for m in modifiers or [] if m.lower() in MODIFIER_CODES]
    return _with_modifiers(mods, steps)


def hover_actions(x: float, y: float) -> list[dict]:
    return _with_modifiers([], [{"type": "pointerMove", "x": round(x), "y": round(y), "origin": "viewport"}])


def text_actions(text: str, submit: bool = False) -> list[dict]:
    steps = []
    for ch in text:
        value = {"\n": "\ue007", "\t": "\ue004"}.get(ch, ch)
        steps += [{"type": "keyDown", "value": value}, {"type": "keyUp", "value": value}]
    if submit:
        steps += [{"type": "keyDown", "value": "\ue007"}, {"type": "keyUp", "value": "\ue007"}]
    return [{"type": "key", "id": "agent-f-keys", "actions": steps}]


def key_actions(keys: str) -> list[dict]:
    steps = []
    for chord in keys.split():
        mods, key = parse_chord(chord)
        steps += [{"type": "keyDown", "value": m} for m in mods]
        steps += [{"type": "keyDown", "value": key}, {"type": "keyUp", "value": key}]
        steps += [{"type": "keyUp", "value": m} for m in reversed(mods)]
    return [{"type": "key", "id": "agent-f-keys", "actions": steps}]


async def perform(session: Session, context: str, actions: list[dict]) -> None:
    try:
        await session.command("input.performActions", {"context": context, "actions": actions})
    finally:
        try:
            await session.command("input.releaseActions", {"context": context}, timeout=5)
        except WebDriverError:
            pass


# Running code. The code runs as statements in an async function, so runtime errors come back as
# values; a WebDriver-level exception therefore means the code didn't compile in that form.

STATEMENT_START = ("return", "if", "for", "while", "do", "switch", "try", "const", "let", "var", "function",
                   "async function", "class", "throw", "break", "continue", "import", "export", "debugger")

SERIALIZE = """(v => {
  const seen = new WeakSet();
  const out = JSON.stringify(v, (k, x) => {
    if (typeof x === "bigint") return String(x);
    if (typeof x === "function") return `[function ${x.name || ""}]`;
    if (x && typeof x === "object") {
      if (typeof Node !== "undefined" && x instanceof Node) return `[${x.nodeName}]`;
      if (x instanceof Map) return Object.fromEntries(x);
      if (x instanceof Set) return Array.from(x);
      if (x instanceof Error) return String(x);
      if (seen.has(x)) return "[Circular]";
      seen.add(x);
    }
    return x;
  });
  return out === undefined ? "undefined" : out;
})"""


def split_last_statement(code: str) -> tuple[str, str] | None:
    """Everything before the last top-level statement, and that statement if it's an expression."""
    src = code.rstrip().rstrip(";").rstrip()
    depth, cut, i = 0, -1, 0
    while i < len(src):
        c = src[i]
        if src.startswith("//", i):
            end = src.find("\n", i)
            i = len(src) if end == -1 else end - 1
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = len(src) if end == -1 else end + 1
        elif c in "\"'`":
            i += 1
            while i < len(src) and src[i] != c:
                i += 2 if src[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0 and c == "}":
                cut = i
        elif depth == 0 and c in ";\n":
            cut = i
        i += 1
    if cut == -1:
        return None
    head, tail = src[:cut + 1], src[cut + 1:].strip()
    if not tail or tail[0] in "{}" or any(tail == w or tail.startswith(w + " ") or tail.startswith(w + "(")
                                          for w in STATEMENT_START):
        return None
    return head, tail


def code_forms(code: str, prelude: str = "") -> list[str]:
    bodies = [f"return ({code}\n);"]
    split = split_last_statement(code)
    if split:
        bodies.append(f"{split[0]}\nreturn ({split[1]}\n);")
    bodies.append(code)
    return [f"""(async function () {{
  {prelude}
  const __value = await (async () => {{ {body}\n }})();
  return {SERIALIZE}(__value);
}})().then(v => JSON.stringify({{ ok: true, value: v }}),
          e => JSON.stringify({{ ok: false, error: e && e.stack ? `${{e}}\\n${{e.stack}}` : String(e) }}))""" for body in bodies]


async def run_code(session: Session, context: str, code: str, prelude: str = "", timeout: float = 10) -> str:
    """Run code the way eval_page does and return its JSON-serialised value."""
    error = None
    for source in code_forms(code, prelude):
        try:
            raw = await session.evaluate(context, source, timeout=timeout)
        except WebDriverError as err:
            if err.code != "script_error":
                raise
            error = err
            continue
        result = json.loads(raw)
        if not result.get("ok"):
            raise WebDriverError("script_error", result.get("error") or "The code threw an exception.")
        return result.get("value", "undefined")
    raise WebDriverError("eval_failed", error.message if error else "The code didn't compile.")


async def chrome_context(session: Session) -> str:
    try:
        contexts = await session.top_level_contexts(chrome=True)
    except WebDriverError as err:
        contexts = []
        if "system access" not in err.message.lower() and err.code not in ("invalid argument", "unsupported operation"):
            raise
    windows = [c for c in contexts if c.get("url", "").startswith("chrome://browser/content/browser.")]
    if not windows:
        raise WebDriverError("system_access_off", (
            "Firefox only lets WebDriver into its own windows when it was started with the environment variable "
            "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1. Set it, quit Firefox completely and start it again."))
    return windows[0]["context"]


CHROME_PRELUDE = ("const window = Services.wm.getMostRecentBrowserWindow(); "
                  "const document = window.document; const gBrowser = window.gBrowser;")


async def eval_chrome(session: Session, code: str, timeout: float = 10) -> str:
    return await run_code(session, await chrome_context(session), code, CHROME_PRELUDE, timeout)


def plain(value: dict):
    """Turn a WebDriver BiDi remote value into Python data."""
    kind = value.get("type")
    if kind in ("undefined", "null"):
        return None
    if kind in ("string", "boolean"):
        return value.get("value")
    if kind == "number":
        v = value.get("value")
        return v if isinstance(v, (int, float)) else None
    if kind == "array":
        return [plain(v) for v in value.get("value") or []]
    if kind == "object":
        return {(k if isinstance(k, str) else str(plain(k))): plain(v) for k, v in value.get("value") or []}
    return value.get("value", f"[{kind}]")
