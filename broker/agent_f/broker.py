"""Entry point: python -m agent_f.broker"""

import asyncio
import logging
import logging.handlers
import os
import sys

import uvicorn

from . import __version__
from .audit import Audit
from .bridge import Hub
from .config import data_dir, load_config, load_token, token_path
from .server import Guard, build_server

log = logging.getLogger("agent_f")


def _setup_logging() -> None:
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        logs / "broker.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler(sys.stderr))


def _pid_file():
    return data_dir() / "broker.pid"


async def run(host: str, mcp_port: int, bridge_port: int, token: str) -> None:
    hub = Hub(token)
    try:
        bound = await hub.start_bridge(host, bridge_port)
    except OSError as err:
        log.error("cannot listen on %s:%s (%s); is the broker already running?", host, bridge_port, err)
        return
    _pid_file().write_text(str(os.getpid()), encoding="utf-8")
    audit = Audit(data_dir() / "logs" / "audit.log")
    app = Guard(build_server(hub, audit).streamable_http_app(), token, mcp_port)
    config = uvicorn.Config(app, host=host, port=mcp_port, log_config=None, lifespan="on")
    server = uvicorn.Server(config)
    log.info("Agent F broker %s: MCP on http://%s:%s/mcp, bridge on %s:%s", __version__, host, mcp_port, host, bound)
    try:
        await server.serve()
    finally:
        await hub.stop_bridge()


def main() -> None:
    # pythonw has no console streams; uvicorn and logging expect them.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    _setup_logging()
    if not token_path().exists():
        log.error("no token at %s; run the installer first", token_path())
        sys.exit(1)
    cfg = load_config()
    try:
        asyncio.run(run(cfg.host, cfg.mcp_port, cfg.bridge_port, load_token()))
    finally:
        try:
            if _pid_file().read_text(encoding="utf-8").strip() == str(os.getpid()):
                _pid_file().unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
