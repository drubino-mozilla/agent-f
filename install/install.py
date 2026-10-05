"""Agent F installer for Windows, macOS and Linux. Standard library only; every step is safe to repeat.

    py -3 install/install.py              install or update the broker and helper, then restart the broker
    py -3 install/install.py --addon      also put the signed add-on into your default Firefox profiles
    py -3 install/install.py --addon --profile "default-nightly"   ...into the named profiles instead
    py -3 install/install.py --restart    only restart the broker
    py -3 install/install.py --uninstall  remove everything except the data folder
    py -3 install/install.py --uninstall --purge   also delete the data folder

(On macOS and Linux, run it with python3.)

The Windows and macOS installers (packaging/) ship a copy of this script next to a Python runtime that already
has the broker in it, plus the signed add-on and a bundle.json. With bundle.json present it uses that runtime
instead of building a venv, takes the add-on from the bundle, and logs to logs/install.log in the data folder.
"""

import argparse
import configparser
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "broker"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import platforms  # noqa: E402
from agent_f.config import (  # noqa: E402
    ADDON_ID, DEFAULT_BRIDGE_PORT, DEFAULT_MCP_PORT, NATIVE_HOST_NAME, Config, config_path, data_dir,
    ensure_token, load_config, mcp_url, save_config,
)

MCP_SERVER_NAME = "agent-f"
CURSOR_MCP_JSON = Path.home() / ".cursor" / "mcp.json"
DEFAULT_UPDATE_URL = "https://drubino-mozilla.github.io/agent-f/updates.json"
OS = platforms.current()


def read_bundle() -> dict | None:
    """What the packaged installers put in bundle.json (version, platform); None in a clone."""
    try:
        return json.loads((REPO / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


BUNDLE = read_bundle()


class Tee:
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text: str) -> None:
        if self.stream is not None:
            self.stream.write(text)
        self.log.write(text)
        self.log.flush()

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()


def log_to_file() -> None:
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = open(logs / "install.log", "a", encoding="utf-8")
    log.write(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} install.py {' '.join(sys.argv[1:])}\n")
    sys.stdout, sys.stderr = Tee(sys.stdout, log), Tee(sys.stderr, log)


def stop_logging() -> None:
    """Close install.log, which Windows won't let --purge delete while it's open."""
    if isinstance(sys.stdout, Tee):
        sys.stdout.log.close()
        sys.stdout, sys.stderr = sys.stdout.stream, sys.stderr.stream


def step(message: str) -> None:
    print(f"- {message}")


def update_url() -> str:
    try:
        manifest = json.loads((REPO / "extension" / "manifest.json").read_text(encoding="utf-8"))
        return manifest["browser_specific_settings"]["gecko"]["update_url"]
    except OSError:
        return DEFAULT_UPDATE_URL


def install_broker(venv: Path, editable: bool) -> None:
    uv = shutil.which("uv")
    python = OS.venv_python(venv)
    if not python.exists():
        if uv:
            subprocess.run([uv, "venv", str(venv), "--python", sys.executable, "-q"], check=True)
        else:
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    target = ["-e", str(REPO / "broker")] if editable else [str(REPO / "broker")]
    if uv:
        subprocess.run([uv, "pip", "install", "-q", "--python", str(python), *target], check=True)
    else:
        subprocess.run([str(python), "-m", "pip", "install", "-q", *target], check=True)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_ports() -> tuple[int, int]:
    reserved = OS.reserved_port_ranges()

    def usable(p: int) -> bool:
        return not any(lo <= p <= hi for lo, hi in reserved) and port_free(p)

    candidate = DEFAULT_MCP_PORT
    while candidate > 20000:
        if usable(candidate) and usable(candidate + 1):
            return candidate, candidate + 1
        candidate -= 2
    raise SystemExit("Could not find two free ports for Agent F.")


def write_config(pythonw: Path) -> Config:
    existing = config_path().exists()
    cfg = load_config()
    if not existing:
        cfg.mcp_port, cfg.bridge_port = choose_ports()
        if (cfg.mcp_port, cfg.bridge_port) != (DEFAULT_MCP_PORT, DEFAULT_BRIDGE_PORT):
            step(f"default ports are taken; using {cfg.mcp_port} and {cfg.bridge_port}")
    cfg.broker_command = [str(pythonw), "-m", "agent_f.broker"]
    save_config(cfg)
    return cfg


def install_native_host(python: Path) -> list[Path]:
    native = data_dir() / "native"
    native.mkdir(parents=True, exist_ok=True)
    host_script = native / "agent_f_host.py"
    shutil.copyfile(REPO / "host" / "agent_f_host.py", host_script)
    launcher = OS.write_launcher(native, python, host_script)
    manifest = native / f"{NATIVE_HOST_NAME}.json"
    manifest.write_text(json.dumps({
        "name": NATIVE_HOST_NAME,
        "description": "Agent F helper",
        "path": str(launcher),
        "type": "stdio",
        "allowed_extensions": [ADDON_ID],
    }, indent=2) + "\n", encoding="utf-8")
    (native / platforms.PAUSE_MARKER).unlink(missing_ok=True)
    return OS.register_native_host(manifest, NATIVE_HOST_NAME)


def _pid_file() -> Path:
    return data_dir() / "broker.pid"


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def stop_broker() -> None:
    try:
        pid = int(_pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if OS.is_python_process(pid):
        OS.kill(pid)
        for _ in range(50):
            if not _listening(load_config().mcp_port):
                break
            time.sleep(0.1)
    _pid_file().unlink(missing_ok=True)


def start_broker(cfg: Config) -> bool:
    OS.start_broker(cfg.broker_command)
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


def firefox_profiles() -> list[dict]:
    """Every profile in every profiles.ini, with whether it is an installation's default."""
    profiles = []
    for root in OS.firefox_roots():
        ini = root / "profiles.ini"
        if not ini.exists():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(ini, encoding="utf-8")
        defaults = set()
        installs = configparser.ConfigParser(interpolation=None)
        installs.read(root / "installs.ini", encoding="utf-8")
        for section in installs.sections():
            if installs.has_option(section, "Default"):
                defaults.add(installs.get(section, "Default").replace("\\", "/"))
        for section in parser.sections():
            if section.startswith("Install") and parser.has_option(section, "Default"):
                defaults.add(parser.get(section, "Default").replace("\\", "/"))
        for section in parser.sections():
            if not section.startswith("Profile") or not parser.has_option(section, "Path"):
                continue
            raw = parser.get(section, "Path").replace("\\", "/")
            relative = parser.get(section, "IsRelative", fallback="1") == "1"
            path = (root / raw) if relative else Path(raw)
            is_default = raw in defaults or (not defaults and parser.get(section, "Default", fallback="0") == "1")
            profiles.append({"name": parser.get(section, "Name", fallback=raw), "path": path,
                             "default": is_default})
    return profiles


def download_signed_addon() -> Path:
    with urllib.request.urlopen(update_url(), timeout=30) as resp:
        updates = json.loads(resp.read())
    entries = updates["addons"][ADDON_ID]["updates"]
    latest = max(entries, key=lambda e: [int(x) if x.isdigit() else 0 for x in e["version"].split(".")])
    with urllib.request.urlopen(latest["update_link"], timeout=120) as resp:
        data = resp.read()
    algorithm, _, expected = latest.get("update_hash", "sha256:").partition(":")
    if expected and hashlib.new(algorithm, data).hexdigest() != expected:
        raise SystemExit("The downloaded add-on doesn't match its published checksum; not installing it.")
    target = data_dir() / f"agent-f-{latest['version']}.xpi"
    target.write_bytes(data)
    step(f"downloaded the signed add-on {latest['version']}")
    return target


def install_addon(names: list[str], xpi: Path | None = None) -> None:
    """Put the add-on into the chosen profiles (default: each installation's default profile), unless a profile
    already has it: an installed copy updates itself, and replacing its file could downgrade it."""
    profiles = firefox_profiles()
    if names:
        chosen = [p for p in profiles if p["name"] in names or p["path"].name in names]
        missing = set(names) - {p["name"] for p in chosen} - {p["path"].name for p in chosen}
        if missing:
            known = ", ".join(sorted(p["name"] for p in profiles)) or "none found"
            raise SystemExit(f"No Firefox profile named {', '.join(sorted(missing))}. Profiles: {known}")
    else:
        chosen = [p for p in profiles if p["default"]]
    if not chosen:
        step("no Firefox profile found; install the add-on from the latest GitHub release instead")
        return
    pending = []
    for profile in chosen:
        folder = profile["path"] / "extensions"
        if (folder / f"{ADDON_ID}.xpi").exists():
            step(f"profile {profile['name']!r} already has the add-on; it updates itself")
        elif (folder / ADDON_ID).is_file():
            step(f"profile {profile['name']!r} runs Agent F from a source folder; left it alone")
        else:
            pending.append(profile)
    if not pending:
        return
    xpi = xpi or download_signed_addon()
    for profile in pending:
        folder = profile["path"] / "extensions"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(xpi, folder / f"{ADDON_ID}.xpi")
        step(f"added the add-on to profile {profile['name']!r}")
    print("  Restart Firefox, then enable Agent F from the notice on Firefox's menu button. After that it updates itself.")


def self_test(python: Path) -> bool:
    result = subprocess.run([str(python), "-m", "agent_f.selftest"], capture_output=True, text=True, check=False)
    print("\nSelf-test (list_browsers):")
    print("  " + (result.stdout.strip() or result.stderr.strip()).replace("\n", "\n  "))
    return result.returncode == 0


def install(args) -> int:
    data_dir().mkdir(parents=True, exist_ok=True)
    step(f"data folder: {data_dir()}")
    if BUNDLE:
        python, pythonw = OS.bundled_python(), OS.bundled_python(windowed=True)
        step(f"Agent F {BUNDLE['version']}, installed in {REPO}")
    else:
        venv = data_dir() / "venv"
        install_broker(venv, editable=not args.no_editable)
        step("broker installed" + (" (editable, from this clone)" if not args.no_editable else ""))
        python, pythonw = OS.venv_python(venv), OS.venv_python(venv, windowed=True)
    token = ensure_token()
    # The helper starts whatever broker_command and the launcher name, so both point at this copy
    # before the old broker stops: a helper that reconnects in between already gets the new one.
    cfg = write_config(pythonw)
    step(f"MCP endpoint: {mcp_url(cfg)}; helper port {cfg.bridge_port}")
    for path in install_native_host(python):
        step(f"native-messaging helper registered: {path}")
    step(OS.set_autostart(cfg.broker_command))
    stop_broker()
    running = start_broker(cfg)
    step("broker running" if running else f"broker did not start; see {data_dir() / 'logs' / 'broker.log'}")
    if not args.no_cursor:
        update_cursor_config(cfg, token)
    if args.addon or args.profile:
        bundled_xpi = REPO / "addon" / "agent-f.xpi"
        install_addon(args.profile or [], bundled_xpi if BUNDLE and bundled_xpi.exists() else None)
    return 0 if self_test(python) and running else 1


def uninstall(args) -> None:
    # Unregister first, and blank the broker command, so a helper Firefox starts meanwhile
    # can't bring the broker back.
    if OS.unregister_native_host(NATIVE_HOST_NAME):
        step("native-messaging helper unregistered")
    if OS.remove_autostart():
        step("autostart removed")
    if config_path().exists():
        cfg = load_config()
        cfg.broker_command = []
        save_config(cfg)
    stop_broker()
    step("broker stopped")
    remove_cursor_config()
    if BUNDLE:
        for path in OS.remove_bundle(REPO):
            step(f"removed {path}")
    if args.purge and data_dir().exists():
        step(f"deleting {data_dir()}")
        stop_logging()
        # The broker can take a moment to let go of its log after it is stopped.
        for _ in range(20):
            shutil.rmtree(data_dir(), ignore_errors=True)
            if not data_dir().exists():
                break
            time.sleep(0.5)
        if data_dir().exists():
            step(f"couldn't delete everything in {data_dir()}; delete it by hand")
    print("\nThe Agent F add-on itself stays installed in Firefox; remove it from about:addons if you like.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Agent F")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--purge", action="store_true", help="with --uninstall, also delete the data folder")
    parser.add_argument("--restart", action="store_true", help="only restart the broker")
    parser.add_argument("--addon", action="store_true",
                        help="put the signed add-on into your default Firefox profiles")
    parser.add_argument("--profile", action="append", metavar="NAME",
                        help="with --addon, the Firefox profile to use (repeatable; implies --addon)")
    parser.add_argument("--list-profiles", action="store_true", help="list Firefox profiles and exit")
    parser.add_argument("--no-editable", action="store_true", help="copy the broker instead of linking this clone")
    parser.add_argument("--no-cursor", action="store_true", help="don't touch ~/.cursor/mcp.json")
    args = parser.parse_args()
    if BUNDLE and not args.list_profiles:
        log_to_file()
    if args.list_profiles:
        for p in firefox_profiles():
            print(f"{'*' if p['default'] else ' '} {p['name']}  {p['path']}")
    elif args.uninstall:
        uninstall(args)
    elif args.restart:
        stop_broker()
        print("Broker running." if start_broker(load_config()) else "Broker did not start; check the log.")
    else:
        sys.exit(install(args))


if __name__ == "__main__":
    main()
