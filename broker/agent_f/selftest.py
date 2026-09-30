"""python -m agent_f.selftest: call list_browsers through the running broker, as an MCP client would."""

import asyncio
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import load_config, load_token, mcp_url


async def _run(tool: str) -> int:
    cfg = load_config()
    headers = {"Authorization": f"Bearer {load_token()}"}
    async with httpx.AsyncClient(headers=headers, timeout=15) as http:
        async with streamable_http_client(mcp_url(cfg), http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, {})
                print(result.content[0].text)
                return 1 if result.isError else 0


def main() -> None:
    tool = sys.argv[1] if len(sys.argv) > 1 else "list_browsers"
    try:
        sys.exit(asyncio.run(_run(tool)))
    except Exception as err:
        print(f"Self-test failed: {err}")
        sys.exit(2)


if __name__ == "__main__":
    main()
