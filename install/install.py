"""Agent F installer (Windows). Standard library only; every step is safe to repeat.

    py -3 install/install.py              install or update, then restart the broker
    py -3 install/install.py --restart    only restart the broker
    py -3 install/install.py --uninstall  remove everything except the data folder
    py -3 install/install.py --uninstall --purge   also delete the data folder
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "broker"))

from agent_f.config import (  # noqa: E402
    ADDON_ID, DEFAULT_BRIDGE_PORT, DEFAULT_MCP_PORT, NATIVE_HOST_NAME, Config, config_path, data_dir,
    ensure_token, load_config, mcp_url, save_config,
)

MCP_SERVER_NAME = "agent-f"
NATIVE_HOST_KEY = rf"Software\Mozilla\NativeMessagingHosts\{NATIVE_HOST_NAME}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Agent F broker"
CURSOR_MCP_JSON = Path.home() / ".cursor" / "mcp.json"


def step(message: str) -> None:
    print(f"- {message}")


def venv_python(venv: Path, windowed: bool = False) -> Path:
    return venv / "Scripts" / ("pythonw.exe" if windowed else "python.exe")


def install_broker(venv: Path, editable: bool) -> None:
    uv = shutil.which("uv")
    if not venv_python(venv).exists():
        if uv:
            subprocess.run([uv, "venv", str(venv), "--python", sys.executable, "-q"], check=True)
        else:
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    target = ["-e", str(REPO / "broker")] if editable else [str(REPO / "broker")]
    if uv:
        subprocess.run([uv, "pip", "install", "-q", "--python", str(venv_python(venv)), *target], check=True)
    else:
        subprocess.run([str(venv_python(venv)), "-m", "pip", "install", "-q", *target], check=True)


def _netsh(*args: str) -> str:
    try:
        return subprocess.run(["netsh", *args], capture_output=True, text=True, check=False).stdout
    except OSError:
        return ""


def reserved_ranges() -> list[tuple[int, int]]:
    ranges = []
    for line in _netsh("interface", "ipv4", "show", "excludedportrange", "protocol=tcp").splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)", line)
        if m:
            ranges.append((int(m.group(1)), int(m.group(2))))
    return ranges


def dynamic_start() -> int:
    m = re.search(r"Start Port\s*:\s*(\d+)", _netsh("interface", "ipv4", "show", "dynamicport", "tcp"))
    return int(m.group(1)) if m else 49152


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_ports() -> tuple[int, int]:
    reserved = reserved_ranges()
    ceiling = dynamic_start()

    def usable(p: int) -> bool:
        return p < ceiling and not any(lo <= p <= hi for lo, hi in reserved) and port_free(p)

    candidate = DEFAULT_MCP_PORT
    while candidate > 20000:
        if usable(candidate) and usable(candidate + 1):
            return candidate, candidate + 1
        candidate -= 2
    raise SystemExit("Could not find two free ports for Agent F.")


def write_config(venv: Path) -> Config:
    existing = config_path().exists()
    cfg = load_config()
    if not existing:
        cfg.mcp_port, cfg.bridge_port = choose_ports()
        if (cfg.mcp_port, cfg.bridge_port) != (DEFAULT_MCP_PORT, DEFAULT_BRIDGE_PORT):
            step(f"default ports are taken; using {cfg.mcp_port} and {cfg.bridge_port}")
    cfg.broker_command = [str(venv_python(venv, windowed=True)), "-m", "agent_f.broker"]
    save_config(cfg)
    return cfg


def install_native_host(venv: Path) -> Path:
    import winreg

    native = data_dir() / "native"
    native.mkdir(parents=True, exist_ok=True)
    host_script = native / "agent_f_host.py"
    shutil.copyfile(REPO / "host" / "agent_f_host.py", host_script)
    launcher = native / "agent_f_host.bat"
    launcher.write_text(f'@echo off\r\n"{venv_python(venv)}" "{host_script}" %*\r\n', encoding="utf-8")
    manifest = native / f"{NATIVE_HOST_NAME}.json"
    manifest.write_text(json.dumps({
        "name": NATIVE_HOST_NAME,
        "description": "Agent F helper",
        "path": str(launcher),
        "type": "stdio",
        "allowed_extensions": [ADDON_ID],
    }, indent=2) + "\n", encoding="utf-8")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, NATIVE_HOST_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
    return manifest


def set_autostart(cfg: Config) -> None:
    import winreg

    command = subprocess.list2cmdline(cfg.broker_command)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)


def _pid_file() -> Path:
    return data_dir() / "broker.pid"


def stop_broker() -> None:
    try:
        pid = int(_pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, check=False).stdout.lower()
    if "python" not in listing:
        _pid_file().unlink(missing_ok=True)
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
    for _ in range(50):
        if not _pid_file().exists() or not _listening(load_config().mcp_port):
            break
        time.sleep(0.1)
    try:
        _pid_file().unlink()
    except OSError:
        pass


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def start_broker(cfg: Config) -> bool:
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(cfg.broker_command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    for _ in range(100):
        if _listening(cfg.mcp_port):
            return True
        time.sleep(0.1)
    return False


def update_cursor_config(cfg: Config, token: str) -> None:
    if not CURSOR_MCP_JSON.parent.exists():
        step("Cursor not found; skipping its MCP configuration")
        return
    config = {"mcpServers": {}}
    if CURSOR_MCP_JSON.exists():
        text = CURSOR_MCP_JSON.read_text(encoding="utf-8")
        config = json.loads(text) if text.strip() else config
        backups = data_dir() / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        (backups / f"mcp.json.{time.strftime('%Y%m%d-%H%M%S')}").write_text(text, encoding="utf-8")
    servers = config.setdefault("mcpServers", {})
    entry = {"url": mcp_url(cfg), "headers": {"Authorization": f"Bearer {token}"}}
    if servers.get(MCP_SERVER_NAME) == entry:
        return
    servers[MCP_SERVER_NAME] = entry
    CURSOR_MCP_JSON.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    step(f"added {MCP_SERVER_NAME!r} to {CURSOR_MCP_JSON}")


def remove_cursor_config() -> None:
    if not CURSOR_MCP_JSON.exists():
        return
    config = json.loads(CURSOR_MCP_JSON.read_text(encoding="utf-8"))
    if config.get("mcpServers", {}).pop(MCP_SERVER_NAME, None) is not None:
        CURSOR_MCP_JSON.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        step(f"removed {MCP_SERVER_NAME!r} from {CURSOR_MCP_JSON}")


def self_test(venv: Path) -> None:
    result = subprocess.run([str(venv_python(venv)), "-m", "agent_f.selftest"],
                            capture_output=True, text=True, check=False)
    print("\nSelf-test (list_browsers):")
    print("  " + (result.stdout.strip() or result.stderr.strip()).replace("\n", "\n  "))


def install(args) -> None:
    venv = data_dir() / "venv"
    data_dir().mkdir(parents=True, exist_ok=True)
    step(f"data folder: {data_dir()}")
    install_broker(venv, editable=not args.no_editable)
    step("broker installed" + (" (editable, from this clone)" if not args.no_editable else ""))
    token = ensure_token()
    cfg = write_config(venv)
    step(f"MCP endpoint: {mcp_url(cfg)}; helper port {cfg.bridge_port}")
    manifest = install_native_host(venv)
    step(f"native-messaging helper registered: {manifest}")
    set_autostart(cfg)
    step("broker starts at logon")
    stop_broker()
    if start_broker(cfg):
        step("broker running")
    else:
        step(f"broker did not start; see {data_dir() / 'logs' / 'broker.log'}")
    if not args.no_cursor:
        update_cursor_config(cfg, token)
    self_test(venv)


def uninstall(args) -> None:
    import winreg

    stop_broker()
    step("broker stopped")
    for root_key, sub, value in ((winreg.HKEY_CURRENT_USER, RUN_KEY, RUN_VALUE),):
        try:
            with winreg.OpenKey(root_key, sub, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, value)
            step("autostart removed")
        except FileNotFoundError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, NATIVE_HOST_KEY)
        step("native-messaging helper unregistered")
    except FileNotFoundError:
        pass
    remove_cursor_config()
    if args.purge and data_dir().exists():
        shutil.rmtree(data_dir(), ignore_errors=True)
        step(f"deleted {data_dir()}")
    print("\nThe Agent F add-on itself stays installed in Firefox; remove it from about:addons if you like.")


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This installer supports Windows only for now.")
    parser = argparse.ArgumentParser(description="Install Agent F")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--purge", action="store_true", help="with --uninstall, also delete the data folder")
    parser.add_argument("--restart", action="store_true", help="only restart the broker")
    parser.add_argument("--no-editable", action="store_true", help="copy the broker instead of linking this clone")
    parser.add_argument("--no-cursor", action="store_true", help="don't touch ~/.cursor/mcp.json")
    args = parser.parse_args()
    if args.uninstall:
        uninstall(args)
    elif args.restart:
        stop_broker()
        print("Broker running." if start_broker(load_config()) else "Broker did not start; check the log.")
    else:
        install(args)


if __name__ == "__main__":
    main()
