"""The audit log: one line per tool call. Arguments that carry page content or secrets are summarised."""

import functools
import json
import logging
import logging.handlers
import re
import time
from pathlib import Path

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

ERROR_CODE_RE = re.compile(r"Error \(([a-z_]+)\)")
# Arguments logged as they are; anything else is logged by length only.
PLAIN_ARGS = {"browser", "tab", "tabs", "window", "ref", "selector", "role", "mode", "action", "direction",
              "button", "count", "full_page", "marks", "format", "network", "console", "bodies", "level",
              "label", "group", "container", "background", "private", "wait", "method", "state", "limit",
              "to_ref", "to_selector", "session_id", "gone", "load", "network_idle", "once", "confirm",
              "type", "headers", "trusted", "names"}
URL_ARGS = {"url"}


class Audit:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._log = logging.getLogger("agent_f.audit")
        self._log.propagate = False
        self._log.setLevel(logging.INFO)
        if not self._log.handlers:
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._log.addHandler(handler)

    def write(self, tool: str, kwargs: dict, outcome: str, started: float, session: str) -> None:
        args = {}
        for key, value in kwargs.items():
            if key in ("ctx", "since") or value is None:
                continue
            if key in PLAIN_ARGS or isinstance(value, (bool, int, float)):
                args[key] = value
            elif key in URL_ARGS:
                args[key] = str(value)[:200]
            else:
                args[key] = f"<{len(str(value))} chars>"
        line = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session": session[:8],
            "tool": tool,
            "args": args,
            "outcome": outcome,
            "ms": round((time.monotonic() - started) * 1000),
        }
        self._log.info(json.dumps(line, ensure_ascii=False))


def _session(kwargs: dict) -> str:
    ctx = kwargs.get("ctx")
    try:
        request = ctx.request_context.request if isinstance(ctx, Context) else None
        return (request.headers.get("mcp-session-id") if request is not None else None) or "default"
    except (AttributeError, LookupError, ValueError):
        return "default"


def audited(mcp, audit: Audit | None):
    """Like mcp.tool(), but each call is written to the audit log."""
    def tool(name: str | None = None):
        def decorator(fn):
            if audit is None:
                return mcp.tool(name=name)(fn)
            tool_name = name or fn.__name__

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                started = time.monotonic()
                session = _session(kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except ToolError as err:
                    m = ERROR_CODE_RE.search(str(err))
                    audit.write(tool_name, kwargs, m.group(1) if m else "error", started, session)
                    raise
                except Exception:
                    audit.write(tool_name, kwargs, "exception", started, session)
                    raise
                audit.write(tool_name, kwargs, "ok", started, session)
                return result
            return mcp.tool(name=name)(wrapper)
        return decorator
    return tool
