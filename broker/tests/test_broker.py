import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from conftest import TOKEN, FakeExtension, running_broker, wait_for_browsers

pytestmark = pytest.mark.anyio

WINDOWS_A = [
    {"id": 1, "focused": True, "incognito": False, "state": "normal", "tabs": [
        {"id": 3, "windowId": 1, "title": "Bug 2067104", "url": "https://bugzilla.mozilla.org/show_bug.cgi?id=2067104",
         "active": False},
        {"id": 4, "windowId": 1, "title": "Slack", "url": "https://app.slack.com/client", "active": True},
    ]},
]
WINDOWS_B = [
    {"id": 1, "focused": False, "incognito": False, "state": "normal", "tabs": [
        {"id": 9, "windowId": 1, "title": "Example", "url": "https://example.com/", "active": True},
    ]},
]


@asynccontextmanager
async def mcp_client(port: int):
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(session: ClientSession, tool: str, **args) -> tuple[str, bool]:
    result = await session.call_tool(tool, args)
    return result.content[0].text, bool(result.isError)


async def test_two_browsers_listed_and_tabs_read():
    async with running_broker() as (hub, mcp_port, bridge_port):
        a = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        b = FakeExtension(bridge_port, "beta-b", "profile-b", WINDOWS_B)
        await a.connect()
        await b.connect()
        await wait_for_browsers(hub, 2)

        async with mcp_client(mcp_port) as client:
            text, is_error = await call(client, "list_browsers")
            assert not is_error
            assert "2 browsers connected" in text
            assert "- nightly-a: Firefox 159.0a1" in text
            assert "- beta-b: Firefox 159.0a1" in text

            text, is_error = await call(client, "list_tabs")
            assert is_error
            assert "browser_ambiguous" in text

            text, is_error = await call(client, "list_tabs", browser="nightly-a")
            assert not is_error
            assert text.startswith("[nightly-a]")
            assert "1 window, 2 tabs." in text
            assert "<<<page-content" in text and "page-content>>>" in text
            assert '* 4 "Slack" https://app.slack.com/client' in text
            assert "Window 1 (focused):" in text

            text, _ = await call(client, "list_tabs")
            assert text.startswith("[nightly-a]"), "the session keeps its current browser"

            text, is_error = await call(client, "label_browser", label="work", browser="beta-b")
            assert not is_error
            assert "Renamed browser beta-b to work." in text
            assert b.label == "work"
            text, _ = await call(client, "list_browsers")
            assert "- work:" in text

        await a.close()
        await b.close()


async def test_changes_since_last_call():
    async with running_broker() as (hub, mcp_port, bridge_port):
        a = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        await a.connect()
        await wait_for_browsers(hub, 1)

        async with mcp_client(mcp_port) as client:
            text, _ = await call(client, "list_tabs")
            assert "Changes since your last call" not in text

            await a.event("tab_created", tab=7, window=1,
                          info={"id": 7, "windowId": 1, "url": "https://example.org/", "title": ""})
            await a.event("tab_removed", tab=3, window=1, data={"isWindowClosing": False})

            text, _ = await call(client, "list_browsers")
            assert "Changes since your last call:" in text
            assert "- Tab 7 opened in window 1: https://example.org/" in text
            assert '- Tab 3 "Bug 2067104" was closed.' in text

            text, _ = await call(client, "list_browsers")
            assert "Changes since your last call" not in text, "changes are reported once"

            async with mcp_client(mcp_port) as other:
                await call(other, "list_browsers")
                await a.event("window_created", window=2)
                text, _ = await call(other, "list_browsers")
                assert "- Window 2 opened." in text, "each session has its own cursor"

            text, _ = await call(client, "list_browsers")
            assert "- Window 2 opened." in text

        await a.close()


def since_token(text: str) -> str:
    last = text.rstrip().splitlines()[-1]
    assert last.startswith("[since=") and last.endswith("]"), last
    return last[len("[since="):-1]


async def test_since_token_keeps_chats_apart_on_a_shared_session():
    async with running_broker() as (hub, mcp_port, bridge_port):
        a = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        await a.connect()
        await wait_for_browsers(hub, 1)
        async with mcp_client(mcp_port) as client:
            text, _ = await call(client, "list_browsers")
            chat_one = since_token(text)

            await a.event("tab_created", tab=7, window=1, info={"id": 7, "windowId": 1, "url": "https://x/"})
            text, _ = await call(client, "list_browsers")
            assert "Tab 7 opened" in text, "a second chat on the same session consumes the session cursor"

            text, _ = await call(client, "list_browsers", since=chat_one)
            assert "Tab 7 opened" in text, "the first chat still sees the change through its own token"
            chat_one = since_token(text)
            text, _ = await call(client, "list_browsers", since=chat_one)
            assert "Changes since your last call" not in text

            text, _ = await call(client, "list_browsers", since="zzzz-3")
            assert "Agent F restarted since your last call" in text
        await a.close()


async def test_new_session_starts_with_no_backlog():
    async with running_broker() as (hub, mcp_port, bridge_port):
        a = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        await a.connect()
        await wait_for_browsers(hub, 1)
        await a.event("tab_created", tab=7, window=1, info={"id": 7, "windowId": 1, "url": "https://x/"})
        async with mcp_client(mcp_port) as client:
            text, _ = await call(client, "list_browsers")
            assert "Changes since your last call" not in text
        await a.close()


async def test_disconnect_is_reported():
    async with running_broker() as (hub, mcp_port, bridge_port):
        a = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        await a.connect()
        await wait_for_browsers(hub, 1)
        async with mcp_client(mcp_port) as client:
            await call(client, "list_browsers")
            await a.close()
            await wait_for_browsers(hub, 0)
            text, _ = await call(client, "list_browsers")
            assert "- Browser nightly-a disconnected." in text
            assert "No browsers connected" in text


async def test_bad_helper_token_is_rejected():
    async with running_broker() as (hub, mcp_port, bridge_port):
        intruder = FakeExtension(bridge_port, "evil", "profile-x", WINDOWS_A, token="1" * 64)
        await intruder.connect()
        await asyncio.sleep(0.2)
        assert hub.browsers == {}
        await intruder.close()


async def test_http_guard():
    async with running_broker() as (hub, mcp_port, bridge_port):
        url = f"http://127.0.0.1:{mcp_port}/mcp"
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        accept = {"Accept": "application/json, text/event-stream"}
        async with httpx.AsyncClient() as http:
            r = await http.post(url, json=body, headers=accept)
            assert r.status_code == 401
            r = await http.post(url, json=body, headers={**accept, "Authorization": f"Bearer {TOKEN}",
                                                         "Origin": "https://evil.example"})
            assert r.status_code == 403
            r = await http.post(url, json=body, headers={**accept, "Authorization": f"Bearer {TOKEN}",
                                                         "Host": f"evil.example:{mcp_port}"})
            assert r.status_code == 403


async def test_same_profile_reconnect_replaces_old_connection():
    async with running_broker() as (hub, mcp_port, bridge_port):
        first = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        await first.connect()
        await wait_for_browsers(hub, 1)
        second = FakeExtension(bridge_port, "nightly-a", "profile-a", WINDOWS_A)
        await second.connect()
        await asyncio.sleep(0.2)
        assert len(hub.browsers) == 1
        assert hub.browsers["profile-a"].label == "nightly-a"
        await second.close()
        await first.close()
