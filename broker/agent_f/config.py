"""Locations, settings and the shared secret. Standard library only, so the installer can import it."""

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ADDON_ID = "agent-f@drubino-mozilla.github.io"
NATIVE_HOST_NAME = "agent_f"
DEFAULT_MCP_PORT = 47470
DEFAULT_BRIDGE_PORT = 47471


def data_dir() -> Path:
    override = os.environ.get("AGENT_F_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "agent-f"


@dataclass
class Config:
    host: str = "127.0.0.1"
    mcp_port: int = DEFAULT_MCP_PORT
    bridge_port: int = DEFAULT_BRIDGE_PORT
    # Command that starts the broker; the helper uses it when the broker isn't running.
    broker_command: list[str] = field(default_factory=list)


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        return Config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = {k: v for k, v in raw.items() if k in Config.__dataclass_fields__}
    return Config(**known)


def save_config(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2) + "\n", encoding="utf-8")


def token_path() -> Path:
    return data_dir() / "token"


def load_token() -> str:
    return token_path().read_text(encoding="utf-8").strip()


def ensure_token() -> str:
    path = token_path()
    if path.exists():
        return load_token()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    path.write_text(token + "\n", encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o600)
    return token


def mcp_url(cfg: Config) -> str:
    return f"http://{cfg.host}:{cfg.mcp_port}/mcp"
