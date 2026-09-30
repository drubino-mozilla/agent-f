import asyncio
import json
import socket
import struct
from contextlib import asynccontextmanager

import pytest
import uvicorn

from agent_f.bridge import Hub
from agent_f.server import Guard, build_server

TOKEN = "0" * 64


@pytest.fixture
def anyio_backend():
    return "asyncio"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def running_broker(audit=None):
    mcp_port = free_port()
    hub = Hub(TOKEN)
    bridge_port = await hub.start_bridge("127.0.0.1", 0)
    app = Guard(build_server(hub, audit).streamable_http_app(), TOKEN, mcp_port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=mcp_port, log_config=None, lifespan="on"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    try:
        yield hub, mcp_port, bridge_port
    finally:
        server.should_exit = True
        await task
        await hub.stop_bridge()


def frame(obj: dict) -> bytes:
    data = json.dumps(obj).encode()
    return struct.pack(">I", len(data)) + data


class FakeError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data or {}


def token(frame_id: int, doc: str, node: int) -> str:
    return f"\x01{frame_id}:{doc}:{node}\x01"


class FakeExtension:
    """Speaks the helper protocol the way the real extension and helper do together."""

    def __init__(self, bridge_port: int, label: str, profile_id: str, windows: list[dict], token: str = TOKEN,
                 handlers: dict | None = None):
        self.bridge_port = bridge_port
        self.label = label
        self.profile_id = profile_id
        self.windows = windows
        self.token = token
        self.handlers = handlers or {}
        self.requests: list[dict] = []
        self._task = None

    def last(self, command: str) -> dict:
        return [r for r in self.requests if r["command"] == command][-1]

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.bridge_port)
        self.writer.write(frame({"type": "auth", "token": self.token}))
        self.writer.write(frame({"type": "hello", "protocol": 1, "profileId": self.profile_id,
                                 "label": self.label, "browser": "Firefox 159.0a1"}))
        await self.writer.drain()
        self._task = asyncio.create_task(self._serve())

    async def _serve(self):
        try:
            while True:
                (length,) = struct.unpack(">I", await self.reader.readexactly(4))
                message = json.loads(await self.reader.readexactly(length))
                self.requests.append(message)
                await self._answer(message)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def _answer(self, message):
        command = message["command"]
        if command in self.handlers:
            try:
                result = self.handlers[command](message["params"])
                # Events the command causes reach the broker before its response, as in Firefox.
                for event in (result or {}).pop("__events__", []):
                    self.writer.write(frame({"type": "event", **event}))
                reply = {"id": message["id"], "type": "response", "result": result}
            except FakeError as err:
                reply = {"id": message["id"], "type": "response",
                         "error": {"code": err.code, "message": err.message, "data": err.data}}
            self.writer.write(frame(reply))
            await self.writer.drain()
            return
        if command == "list_tabs":
            result = {"focusedWindow": self.windows[0]["id"] if self.windows else None,
                      "firefoxFocused": True, "windows": self.windows, "groups": [], "containers": []}
        elif command == "set_label":
            self.label = message["params"]["label"]
            result = {"label": self.label}
        else:
            self.writer.write(frame({"id": message["id"], "type": "response",
                                     "error": {"code": "unknown_command", "message": command}}))
            await self.writer.drain()
            return
        self.writer.write(frame({"id": message["id"], "type": "response", "result": result}))
        await self.writer.drain()

    async def event(self, event: str, **fields):
        self.writer.write(frame({"type": "event", "event": event, **fields}))
        await self.writer.drain()
        await asyncio.sleep(0.05)

    async def close(self):
        self.writer.close()
        if self._task:
            await self._task


async def wait_for_browsers(hub: Hub, count: int):
    for _ in range(100):
        if len(hub.browsers) == count:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"expected {count} browsers, have {len(hub.browsers)}")
