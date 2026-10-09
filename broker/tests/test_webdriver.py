import asyncio
import base64
import hashlib
import json
import os
import struct

import pytest

from agent_f import webdriver
from conftest import FakeExtension, running_broker, wait_for_browsers
from test_broker import WINDOWS_A, call, mcp_client

pytestmark = pytest.mark.anyio

TAB4 = {"id": 4, "windowId": 1, "title": "Slack", "url": "https://app.slack.com/client", "active": True}


class FakeRemoteAgent:
    """Speaks enough WebDriver BiDi, over a real WebSocket, to stand in for Firefox's remote agent."""

    def __init__(self, contexts: list[dict], time_origins: dict[str, float], busy: bool = False):
        self.contexts = contexts
        self.time_origins = time_origins
        self.busy = busy
        self.commands: list[dict] = []
        self.server = None
        self.port = 0

    async def start(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    def methods(self) -> list[str]:
        return [c["method"] for c in self.commands]

    async def _serve(self, reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        key = next(line.split(":", 1)[1].strip() for line in head.split("\r\n") if line.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1((key + webdriver.WS_GUID).encode()).digest()).decode()
        writer.write(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        await writer.drain()
        try:
            while True:
                b1, b2 = await reader.readexactly(2)
                length = b2 & 0x7F
                if length == 126:
                    (length,) = struct.unpack("!H", await reader.readexactly(2))
                elif length == 127:
                    (length,) = struct.unpack("!Q", await reader.readexactly(8))
                mask = await reader.readexactly(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(await reader.readexactly(length)))
                if b1 & 0x0F == 0x8:
                    break
                message = json.loads(payload)
                self.commands.append(message)
                reply = self._answer(message)
                data = json.dumps({"id": message["id"], **reply}).encode()
                header = struct.pack("!BB", 0x81, len(data)) if len(data) < 126 else struct.pack("!BBH", 0x81, 126, len(data))
                writer.write(header + data)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        writer.close()

    def _answer(self, message: dict) -> dict:
        method, params = message["method"], message["params"]
        if method == "session.new":
            if self.busy:
                return {"type": "error", "error": "session not created", "message": "Maximum number of active sessions"}
            return {"type": "success", "result": {"sessionId": "s1", "capabilities": {"moz:processID": 1}}}
        if method == "browsingContext.getTree":
            return {"type": "success", "result": {"contexts": self.contexts}}
        if method == "script.evaluate":
            origin = self.time_origins.get(params["target"]["context"])
            return {"type": "success", "result": {"type": "success", "result": {"type": "number", "value": origin}}}
        return {"type": "success", "result": {}}


async def with_agent(monkeypatch, tmp_path, agent: FakeRemoteAgent):
    await agent.start()
    profile = tmp_path / "abcd.default-nightly"
    profile.mkdir()
    (profile / "WebDriverBiDiServer.json").write_text(json.dumps({"ws_host": "127.0.0.1", "ws_port": agent.port}))
    monkeypatch.setattr(webdriver, "profile_roots", lambda: [tmp_path])


async def test_trusted_click_goes_through_webdriver_to_the_matching_document(monkeypatch, tmp_path):
    agent = FakeRemoteAgent(
        contexts=[{"context": "ctx-other", "url": "https://app.slack.com/client"},
                  {"context": "ctx-slack", "url": "https://app.slack.com/client"}],
        time_origins={"ctx-other": 1000.0, "ctx-slack": 2000.25})
    await with_agent(monkeypatch, tmp_path, agent)
    seen = {}

    def begin(params):
        seen["begin"] = params
        return {"token": "t1", "x": 10.4, "y": 20.6, "url": "https://app.slack.com/client", "timeOrigin": 2000.25,
                "target": 'button "Send"', "tab": TAB4, "effects": []}

    def end(params):
        seen["end"] = params
        return {"target": params["target"], "tab": TAB4, "effects": ["A dialog \"Sent\" appeared."]}

    async with running_broker() as (hub, mcp_port, bridge_port):
        ext = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A,
                            handlers={"trusted_begin": begin, "trusted_end": end})
        await ext.connect()
        await wait_for_browsers(hub, 1)
        try:
            async with mcp_client(mcp_port) as client:
                text, is_error = await call(client, "click", tab=4, selector="#send", trusted=True, modifiers=["Shift"])
                assert not is_error, text
                assert 'Clicked button "Send".' in text
                assert 'Effects:\n- A dialog "Sent" appeared.' in text
        finally:
            await ext.close()
            await agent.stop()

    assert seen["begin"]["action"] == "click" and seen["begin"]["selector"] == "#send"
    assert seen["end"]["token"] == "t1" and "abort" not in seen["end"]
    assert agent.methods() == ["session.new", "browsingContext.getTree", "script.evaluate", "script.evaluate",
                               "input.performActions", "input.releaseActions", "session.end"]
    perform = next(c for c in agent.commands if c["method"] == "input.performActions")["params"]
    assert perform["context"] == "ctx-slack", "the document whose time origin matches"
    keys, pointer = perform["actions"]
    assert [a["type"] for a in keys["actions"]] == ["keyDown", "pause", "pause", "pause", "keyUp"]
    assert [a["type"] for a in pointer["actions"]] == ["pause", "pointerMove", "pointerDown", "pointerUp", "pause"]
    assert pointer["actions"][1] == {"type": "pointerMove", "x": 10, "y": 21, "origin": "viewport"}


async def test_trusted_input_explains_a_busy_webdriver_and_releases_the_page(monkeypatch, tmp_path):
    agent = FakeRemoteAgent(contexts=[], time_origins={}, busy=True)
    await with_agent(monkeypatch, tmp_path, agent)
    ended = {}

    def begin(params):
        return {"token": "t2", "x": 1, "y": 1, "url": "https://app.slack.com/client", "timeOrigin": 5.0,
                "target": "textbox", "tab": TAB4, "effects": []}

    def end(params):
        ended.update(params)
        return {"aborted": True, "tab": TAB4, "effects": []}

    async with running_broker() as (hub, mcp_port, bridge_port):
        ext = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A,
                            handlers={"trusted_begin": begin, "trusted_end": end})
        await ext.connect()
        await wait_for_browsers(hub, 1)
        try:
            async with mcp_client(mcp_port) as client:
                text, is_error = await call(client, "type", tab=4, selector="input", text="hi", trusted=True)
                assert is_error
                assert "webdriver_busy" in text and "firefox-devtools-mcp" in text
        finally:
            await ext.close()
            await agent.stop()
    assert ended == {**ended, "token": "t2", "abort": True}


async def test_unknown_arguments_and_missing_options_are_refused():
    async with running_broker() as (hub, mcp_port, bridge_port):
        ext = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        await ext.connect()
        await wait_for_browsers(hub, 1)
        try:
            async with mcp_client(mcp_port) as client:
                text, is_error = await call(client, "select_option", tab=4, ref="e1", option="Search")
                assert is_error
                assert "select_option doesn't take 'option'" in text and "labels" in text
                text, is_error = await call(client, "select_option", tab=4, selector="select")
                assert is_error and "bad_arguments" in text and "Pass values or labels" in text
        finally:
            await ext.close()


def test_last_statement_is_returned():
    assert webdriver.split_last_statement("const a = 1; a * 2;") == ("const a = 1;", "a * 2")
    assert webdriver.split_last_statement("for (const x of [1]) f(x);\n'done'") == ("for (const x of [1]) f(x);\n", "'done'")
    assert webdriver.split_last_statement("x(); return 3") is None
    assert webdriver.split_last_statement("if (a) { b(); }") is None
    assert webdriver.split_last_statement("f('a;b')") is None
    assert webdriver.split_last_statement("function f() { return 1 } f()") == ("function f() { return 1 }", "f()")


def test_key_chords():
    assert webdriver.parse_chord("Control+a") == (["\ue009"], "a")
    assert webdriver.parse_chord("Shift++") == (["\ue008"], "+")
    actions = webdriver.key_actions("Enter Control+Shift+t")[0]["actions"]
    assert [a["value"] for a in actions] == ["\ue007", "\ue007", "\ue009", "\ue008", "t", "t", "\ue008", "\ue009"]
    with pytest.raises(webdriver.WebDriverError):
        webdriver.parse_chord("Hyper+x")


def test_endpoints_come_from_profiles_with_a_default(tmp_path):
    assert webdriver.find_endpoints([tmp_path]) == [webdriver.Endpoint("127.0.0.1", 9222)]
    (tmp_path / "p1").mkdir()
    (tmp_path / "p1" / "WebDriverBiDiServer.json").write_text('{"ws_host": "localhost", "ws_port": 9333}')
    assert webdriver.find_endpoints([tmp_path]) == [webdriver.Endpoint("127.0.0.1", 9333, str(tmp_path / "p1"))]


async def test_large_messages_round_trip_over_the_websocket():
    received = {}

    async def serve(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        key = next(l.split(":", 1)[1].strip() for l in head.split("\r\n") if l.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1((key + webdriver.WS_GUID).encode()).digest()).decode()
        writer.write(f"HTTP/1.1 101 OK\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        b1, b2 = await reader.readexactly(2)
        (length,) = struct.unpack("!Q", await reader.readexactly(8))
        mask = await reader.readexactly(4)
        received["text"] = bytes(b ^ mask[i % 4] for i, b in enumerate(await reader.readexactly(length))).decode()
        reply = os.urandom(40000).hex().encode()
        received["reply"] = reply.decode()
        half = len(reply) // 2
        writer.write(struct.pack("!BBH", 0x01, 126, half) + reply[:half])
        writer.write(struct.pack("!BB", 0x89, 0))
        writer.write(struct.pack("!BBQ", 0x80, 127, len(reply) - half) + reply[half:])
        await writer.drain()
        await reader.readexactly(6)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    ws = await webdriver.WebSocket.connect("127.0.0.1", server.sockets[0].getsockname()[1], "/session")
    big = "x" * 70000
    await ws.send(big)
    assert await ws.recv() == received["reply"], "fragments are joined and pings answered"
    assert received["text"] == big
    await ws.close()
    server.close()
