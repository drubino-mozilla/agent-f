"""Throwaway Firefox profiles for developing Agent F. They load the extension straight from this clone.

    py -3 tools/dev_profile.py create NAME [--firefox PATH]
    py -3 tools/dev_profile.py launch NAME [URL ...]
    py -3 tools/dev_profile.py restart NAME [URL ...]   reload the extension after changing it
    py -3 tools/dev_profile.py stop NAME
    py -3 tools/dev_profile.py delete NAME

Profiles live in the Agent F data folder, not in Firefox's profile manager.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "broker"))

from agent_f.config import ADDON_ID, data_dir  # noqa: E402

DEFAULT_FIREFOX = Path(r"C:\Program Files\Firefox Nightly\firefox.exe")

DEV_PREFS = {
    "xpinstall.signatures.required": False,
    "extensions.autoDisableScopes": 0,
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "browser.startup.homepage_override.mstone": "ignore",
    "datareporting.policy.dataSubmissionEnabled": False,
    "toolkit.telemetry.reportingpolicy.firstRun": False,
    "devtools.chrome.enabled": True,
    "devtools.debugger.remote-enabled": True,
    "browser.startup.page": 0,
    "browser.startup.homepage": "about:blank",
    "browser.sessionstore.resume_from_crash": False,
}


def profiles_root() -> Path:
    return data_dir() / "dev-profiles"


def profile_dir(name: str) -> Path:
    return profiles_root() / name


def _settings_path(name: str) -> Path:
    return profile_dir(name) / "agent-f-dev.json"


def write_prefs(path: Path) -> None:
    prefs = "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in DEV_PREFS.items())
    (path / "user.js").write_text(prefs, encoding="utf-8")


def create(name: str, firefox: Path) -> Path:
    path = profile_dir(name)
    (path / "extensions").mkdir(parents=True, exist_ok=True)
    write_prefs(path)
    # A link file named after the add-on ID, containing the path to the unpacked extension.
    (path / "extensions" / ADDON_ID).write_text(str(REPO / "extension"), encoding="utf-8")
    _settings_path(name).write_text(json.dumps({"firefox": str(firefox)}), encoding="utf-8")
    return path


def _running_pids(name: str) -> list[str]:
    marker = str(profile_dir(name)).lower()
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='firefox.exe'\" | "
         "ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine }"],
        capture_output=True, text=True, check=False)
    pids = []
    for line in result.stdout.splitlines():
        pid, _, command = line.partition(" ")
        if marker in command.lower():
            pids.append(pid)
    return pids


def stop(name: str) -> bool:
    """Stop the Firefox running this dev profile, if any, and wait until it has fully exited,
    so a relaunch doesn't find the profile still locked."""
    pids = _running_pids(name)
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True, check=False)
    for _ in range(50):
        if not _running_pids(name):
            break
        time.sleep(0.2)
    return bool(pids)


def launch(name: str, urls: list[str]) -> None:
    path = profile_dir(name)
    if not path.exists():
        raise SystemExit(f"No dev profile named {name!r}; create it first.")
    write_prefs(path)
    firefox = Path(json.loads(_settings_path(name).read_text(encoding="utf-8"))["firefox"])
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen([str(firefox), "-profile", str(path), "-no-remote", *urls],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags)


def delete(name: str) -> None:
    shutil.rmtree(profile_dir(name), ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Agent F development profiles")
    sub = parser.add_subparsers(dest="action", required=True)
    p_create = sub.add_parser("create")
    p_create.add_argument("name")
    p_create.add_argument("--firefox", type=Path, default=DEFAULT_FIREFOX)
    p_launch = sub.add_parser("launch")
    p_launch.add_argument("name")
    p_launch.add_argument("urls", nargs="*")
    p_restart = sub.add_parser("restart", help="stop the profile's Firefox and launch it again, to reload the extension")
    p_restart.add_argument("name")
    p_restart.add_argument("urls", nargs="*")
    p_stop = sub.add_parser("stop")
    p_stop.add_argument("name")
    p_delete = sub.add_parser("delete")
    p_delete.add_argument("name")
    args = parser.parse_args()

    if args.action == "create":
        if not args.firefox.exists():
            raise SystemExit(f"Firefox not found at {args.firefox}; pass --firefox.")
        print(f"Created {create(args.name, args.firefox)}")
    elif args.action == "launch":
        launch(args.name, args.urls)
    elif args.action == "restart":
        if stop(args.name):
            time.sleep(1)
        launch(args.name, args.urls)
    elif args.action == "stop":
        stop(args.name)
    elif args.action == "delete":
        stop(args.name)
        delete(args.name)
        print(f"Deleted dev profile {args.name}")


if __name__ == "__main__":
    main()
