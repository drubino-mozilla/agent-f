import base64

import pytest

from conftest import FakeError, FakeExtension, running_broker, token, wait_for_browsers
from test_broker import WINDOWS_A, call, mcp_client

pytestmark = pytest.mark.anyio

TAB4 = {"id": 4, "windowId": 1, "title": "Slack", "url": "https://app.slack.com/client", "active": True,
        "userTab": True}
TAB9 = {"id": 9, "windowId": 1, "title": "Example", "url": "https://example.com/", "active": False}
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def snapshot_handler(params):
    return {"text": f'- main\n  - button "Send" [ref={token(0, "aaaa1111", 5)}]\n'
                    f'  - textbox "Message" [ref={token(3, "bbbb2222", 2)}]',
            "truncated": False, "tab": TAB4, "effects": []}


async def start(handlers):
    ctx = running_broker()
    hub, mcp_port, bridge_port = await ctx.__aenter__()
    ext = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A, handlers=handlers)
    await ext.connect()
    await wait_for_browsers(hub, 1)
    return ctx, hub, mcp_port, ext


async def test_refs_are_global_and_route_to_their_tab_and_frame():
    clicked = {}

    def click(params):
        clicked.update(params)
        return {"target": f'button "Send" [ref={token(0, "aaaa1111", 5)}]', "tab": TAB4,
                "effects": [f'Focus moved to textbox "Message" [ref={token(0, "aaaa1111", 7)}].']}

    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot_handler, "click": click})
    try:
        async with mcp_client(mcp_port) as client:
            text, is_error = await call(client, "snapshot", tab=4)
            assert not is_error, text
            assert '[nightly-a] tab 4 "Slack" https://app.slack.com/client (the tab you have open)' in text
            assert 'button "Send" [ref=e1]' in text
            assert 'textbox "Message" [ref=e2]' in text
            assert "\x01" not in text

            text, is_error = await call(client, "click", ref="e2")
            assert not is_error, text
            assert clicked == {**clicked, "tab": 4, "frame": 3, "node": 2, "doc": "bbbb2222"}
            assert "Clicked button \"Send\" [ref=e1]." in text, "the same element keeps its ref"
            assert "Effects:\n- Focus moved to textbox \"Message\" [ref=e3]." in text

            text, _ = await call(client, "snapshot", tab=4)
            assert "[ref=e1]" in text and "[ref=e2]" in text, "refs are stable across snapshots of one document"
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)


async def test_a_calls_own_events_reach_only_other_chats():
    def click(params):
        return {"target": "link", "tab": TAB4, "effects": ["Opened tab 12."], "__events__": [
            {"event": "tab_created", "tab": 12, "window": 1,
             "info": {"id": 12, "windowId": 1, "url": "https://example.com/", "openerTabId": 4}},
        ]}

    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot_handler, "click": click})
    try:
        async with mcp_client(mcp_port) as client:
            text, _ = await call(client, "snapshot", tab=4)
            other_chat = text.rstrip().splitlines()[-1][len("[since="):-1]
            text, _ = await call(client, "click", ref="e1", since=other_chat)
            assert "Effects:\n- Opened tab 12." in text
            assert "Changes since your last call" not in text, "a call's own events aren't outside changes"
            text, _ = await call(client, "list_browsers", since=other_chat)
            assert "- Another Agent F call opened tab 12 in window 1: https://example.com/" in text
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)


async def test_stale_document_returns_a_fresh_snapshot():
    def click(params):
        raise FakeError("stale_document", "The page changed (it navigated or reloaded) since that ref was issued.")

    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot_handler, "click": click})
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", tab=4)
            text, is_error = await call(client, "click", ref="e1")
            assert is_error
            assert "Error (stale_ref): The page changed" in text
            assert "A fresh snapshot, with new refs:" in text
            assert "[ref=e1]" in text
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)


async def test_removed_element_offers_candidates():
    def click(params):
        raise FakeError("node_gone", 'The button "Send" is no longer on the page.',
                        {"candidates": [f'- button "Send" [ref={token(0, "aaaa1111", 9)}]']})

    ctx, hub, mcp_port, ext = await start({"snapshot": snapshot_handler, "click": click})
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", tab=4)
            text, is_error = await call(client, "click", ref="e1")
            assert is_error
            assert "Possible matches on the page now:\n- button \"Send\" [ref=e3]" in text
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)


async def test_first_call_without_tab_uses_the_users_tab_and_then_sticks():
    ctx, hub, mcp_port, ext = await start({
        "snapshot": snapshot_handler,
        "resolve_tab": lambda p: {"tab": TAB4},
    })
    try:
        async with mcp_client(mcp_port) as client:
            text, _ = await call(client, "snapshot")
            assert "(no current tab yet, using the tab you have open)" in text
            text, _ = await call(client, "snapshot")
            assert "(your current tab)" in text
            assert len([r for r in ext.requests if r["command"] == "resolve_tab"]) == 1
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)


async def test_current_tab_is_kept_per_browser():
    ctx, hub, mcp_port, a = await start({"snapshot": snapshot_handler})
    b = FakeExtension(a.bridge_port, "beta-b", "profile-b", WINDOWS_A,
                      handlers={"snapshot": lambda p: {"text": "- main", "tab": {**TAB9, "id": p["tab"]}}})
    await b.connect()
    await wait_for_browsers(hub, 2)
    try:
        async with mcp_client(mcp_port) as client:
            await call(client, "snapshot", browser="nightly-a", tab=4)
            await call(client, "snapshot", browser="beta-b", tab=9)
            text, _ = await call(client, "snapshot", browser="nightly-a")
            assert a.last("snapshot")["params"]["tab"] == 4
            assert "(your current tab)" in text
    finally:
        await b.close()
        await a.close()
        await ctx.__aexit__(None, None, None)


async def test_navigate_never_falls_back_to_the_users_tab():
    ctx, hub, mcp_port, ext = await start({})
    try:
        async with mcp_client(mcp_port) as client:
            text, is_error = await call(client, "navigate", url="https://example.org/")
            assert is_error
            assert "no_tab" in text
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)


async def test_unknown_ref():
    ctx, hub, mcp_port, ext = await start({})
    try:
        async with mcp_client(mcp_port) as client:
            text, is_error = await call(client, "click", ref="e999")
            assert is_error
            assert "unknown_ref" in text
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)


async def test_open_tab_becomes_current_and_screenshot_returns_an_image():
    ctx, hub, mcp_port, ext = await start({
        "open_tab": lambda p: {"tab": TAB9, "effects": []},
        "screenshot": lambda p: {"image": PNG, "mime": "image/png", "tab": TAB9, "effects": [],
                                 "legend": [{"n": 1, "token": token(0, "cccc3333", 4)}]},
    })
    try:
        async with mcp_client(mcp_port) as client:
            text, is_error = await call(client, "open_tab", url="https://example.com/")
            assert not is_error, text
            assert "Opened tab 9 in the background." in text
            assert ext.last("open_tab")["params"]["group"] == "Agent F"

            result = await client.call_tool("screenshot", {"marks": True})
            assert not result.isError
            assert ext.last("screenshot")["params"]["tab"] == 9, "the new tab is the current tab"
            kinds = [c.type for c in result.content]
            assert kinds == ["text", "image"]
            assert "Marks: 1=e1" in result.content[0].text
    finally:
        await ext.close()
        await ctx.__aexit__(None, None, None)
