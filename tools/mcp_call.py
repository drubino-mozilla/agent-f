"""Call Agent F tools through the running broker, as an MCP client would. For development.

    <venv python> tools/mcp_call.py TOOL [JSON_ARGS] [TOOL [JSON_ARGS] ...] [--images DIR]

Calls run in order in one MCP session. Each call gets the since token from the previous result,
unless its arguments set one. Images from screenshots are saved to --images (default: the current folder).
"""

import argparse
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "broker"))

import httpx  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from agent_f.config import load_config, load_token, mcp_url  # noqa: E402

SINCE_RE = re.compile(r"\[since=([0-9a-f]+-\d+)\]\s*$")
REF_RE = re.compile(r"\[ref=(e\d+)\]")


def resolve_placeholders(args: dict, last_text: str) -> dict:
    """Replace "@ref:TEXT" with the ref on the first line of the previous result that contains TEXT."""
    out = {}
    for key, value in args.items():
        if isinstance(value, str) and value.startswith("@ref:"):
            needle = value[len("@ref:"):]
            for line in last_text.splitlines():
                m = REF_RE.search(line)
                if m and needle in line:
                    value = m.group(1)
                    break
            else:
                return None
        out[key] = value
    return out


def parse_calls(items: list[str]) -> list[tuple[str, dict]]:
    calls = []
    i = 0
    while i < len(items):
        tool = items[i]
        args = {}
        if i + 1 < len(items) and items[i + 1].lstrip().startswith("{"):
            args = json.loads(items[i + 1])
            i += 1
        calls.append((tool, args))
        i += 1
    return calls


async def run(calls: list[tuple[str, dict]], images: Path) -> int:
    headers = {"Authorization": f"Bearer {load_token()}"}
    status = 0
    since = None
    shot = 0
    async with httpx.AsyncClient(headers=headers, timeout=120) as http:
        async with streamable_http_client(mcp_url(load_config()), http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                last_text = ""
                for tool, args in calls:
                    resolved = resolve_placeholders(args, last_text)
                    if resolved is None:
                        print(f"=== {tool}: skipped, no matching ref in the previous result for {args}\n")
                        status = 1
                        continue
                    args = resolved
                    if since and "since" not in args:
                        args = {**args, "since": since}
                    result = await session.call_tool(tool, args)
                    last_text = "\n".join(i.text for i in result.content if i.type == "text")
                    print(f"=== {tool} {json.dumps({k: v for k, v in args.items() if k != 'since'})}"
                          + ("  [ERROR]" if result.isError else ""))
                    for item in result.content:
                        if item.type == "text":
                            print(item.text)
                            m = SINCE_RE.search(item.text)
                            if m:
                                since = m.group(1)
                        elif item.type == "image":
                            shot += 1
                            images.mkdir(parents=True, exist_ok=True)
                            ext = item.mimeType.split("/")[-1]
                            path = images / f"shot-{shot}.{ext}"
                            path.write_bytes(base64.b64decode(item.data))
                            print(f"(image saved to {path})")
                    if result.isError:
                        status = 1
                    print()
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Call Agent F tools")
    parser.add_argument("calls", nargs="*")
    parser.add_argument("--file", type=Path, help='JSON list of calls: [["tool", {"arg": 1}], ...]')
    parser.add_argument("--images", type=Path, default=Path.cwd())
    args = parser.parse_args()
    calls = parse_calls(args.calls)
    if args.file:
        calls += [(c[0], c[1] if len(c) > 1 else {}) for c in json.loads(args.file.read_text(encoding="utf-8"))]
    sys.exit(asyncio.run(run(calls, args.images)))


if __name__ == "__main__":
    main()
