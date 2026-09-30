import base64
import json
import logging

import pytest

from agent_f.audit import Audit
from conftest import FakeError, FakeExtension, running_broker, token, wait_for_browsers
from test_broker import WINDOWS_A, call, mcp_client
from test_tools import TAB4, snapshot_handler

pytestmark = pytest.mark.anyio


async def start(handlers, audit=None):
    ctx = running_broker(audit)
    hub, mcp_port, bridge_port = await ctx.__aenter__()
    ext = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A, handlers=handlers)
    await ext.connect()
    await wait_for_browsers(hub, 1)
    return ctx, hub, mcp_port, ext


async def stop(ctx, ext):
    await ext.close()
    await ctx.__aexit__(None, None, None)


async def test_upload_sends_files_in_chunks(tmp_path):
    data = bytes(range(256)) * 5000  # 1.28 MB, three chunks
    path = tmp_path / "report.bin"
    path.write_bytes(data)
    chunks = {}

    def upload_chunk(p):
        chunks.setdefault(p["upload"], {})[p["index"]] = base64.b64decode(p["data"])
        assert p["name"] == "report.bin"
        return {"received": p["index"]}

    def upload_file(p):
        assert p["node"] == 5 and p["tab"] == 4
        (upload_id,) = p["uploads"]
        joined = b"".join(chunks[upload_id][i] for i in sorted(chunks[upload_id]))
        assert joined == data
        return {"target": "button", "how": "set the file input", "names": [f"report.bin ({len(joined)} bytes)"],
                "tab": TAB4, "effects": []}

    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot_handler, "upload_chunk": upload_chunk,
                                           "upload_file": upload_file})
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", tab=4)
            text, is_error = await call(client, "upload_file", ref="e1", paths=[str(path)])
            assert not is_error, text
            assert f"Uploaded report.bin ({len(data)} bytes)" in text
            assert len(next(iter(chunks.values()))) == 3
    finally:
        await stop(ctx, ext)


async def test_upload_refuses_agent_f_files(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_F_HOME", str(tmp_path))
    secret = tmp_path / "token"
    secret.write_text("x")
    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot_handler})
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", tab=4)
            text, is_error = await call(client, "upload_file", ref="e1", paths=[str(secret)])
            assert is_error and "forbidden_file" in text
    finally:
        await stop(ctx, ext)


async def test_drag_target_must_share_the_frame():
    def snapshot(p):
        return {"text": f'- button "A" [ref={token(0, "aaaa1111", 1)}]\n- button "B" [ref={token(2, "bbbb2222", 2)}]',
                "tab": TAB4}

    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot,
                                           "drag": lambda p: {"from": "A", "to": "B", "how": "dragged", "tab": TAB4}})
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", tab=4)
            text, is_error = await call(client, "drag", ref="e1", to_ref="e2")
            assert is_error and "same page and frame" in text
    finally:
        await stop(ctx, ext)


async def test_paused_browser_explains_itself():
    def click(p):
        raise FakeError("paused", "The user paused Agent F in this Firefox (toolbar button). Ask them to resume it.")

    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot_handler, "click": click})
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", tab=4)
            text, is_error = await call(client, "click", ref="e1")
            assert is_error and "Error (paused): The user paused Agent F" in text
    finally:
        await stop(ctx, ext)


async def test_get_network_lists_requests_and_the_next_cursor():
    def get_network(p):
        assert p["since_seq"] == 3 and p["bodies"] is True
        return {"requests": [{"seq": 4, "method": "POST", "url": "https://api.example.com/save", "type": "xmlhttprequest",
                              "status": 201, "error": None, "contentType": "application/json; charset=utf-8",
                              "duration": 42, "pending": False, "requestBody": '{"password":"redacted"}',
                              "responseBody": '{"ok":true}'}],
                "total": 1, "lastSeq": 4, "tab": TAB4, "effects": []}

    ctx, hub, mcp_port, ext = await start({"get_network": get_network})
    try:
        async with mcp_client(mcp_port) as client:
            text, is_error = await call(client, "get_network", tab=4, after=3, bodies=True)
            assert not is_error, text
            assert "#4 POST 201 xmlhttprequest 42 ms application/json https://api.example.com/save" in text
            assert '{"ok":true}' in text
            assert "Pass after=4 next time" in text
    finally:
        await stop(ctx, ext)


async def test_audit_log_records_calls_without_content(tmp_path):
    log_path = tmp_path / "audit.log"
    audit = Audit(log_path)
    ctx, hub, mcp_port, ext = await start({
        "snapshot": snapshot_handler,
        "type": lambda p: {"target": "textbox", "value": "x", "tab": TAB4},
    }, audit=audit)
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", tab=4)
            await call(client, "type", ref="e1", text="my secret words")
            await call(client, "click", ref="e999")
        for handler in logging.getLogger("agent_f.audit").handlers:
            handler.flush()
        lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        tools = [(entry["tool"], entry["outcome"]) for entry in lines]
        assert ("snapshot", "ok") in tools
        assert ("type", "ok") in tools
        assert ("click", "unknown_ref") in tools
        typed = next(entry for entry in lines if entry["tool"] == "type")
        assert typed["args"]["text"] == "<15 chars>"
        assert "my secret words" not in log_path.read_text(encoding="utf-8")
    finally:
        await stop(ctx, ext)
        for handler in list(logging.getLogger("agent_f.audit").handlers):
            handler.close()
            logging.getLogger("agent_f.audit").removeHandler(handler)
