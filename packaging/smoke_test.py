"""End-to-end test of a built installer, for a throwaway CI machine (it refuses to run elsewhere).

    python packaging/smoke_test.py windows dist/Agent-F-Windows.exe
    python packaging/smoke_test.py macos dist/Agent-F-macOS.pkg

It sets up a Firefox profile and a Cursor folder, installs, and checks the broker, the helper registration, the
add-on and Cursor's entry. Then it starts Firefox headless and waits for the add-on to connect through the
helper, runs the installer again while Firefox stays connected (the update path), and waits for it to
reconnect. Last, it uninstalls and checks that nothing is left running or registered.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "broker"))
sys.path.insert(0, str(REPO / "install"))

import platforms  # noqa: E402
from agent_f.config import ADDON_ID, NATIVE_HOST_NAME, data_dir  # noqa: E402

OS = platforms.current()
HOME = Path.home()
PROFILE = OS.firefox_roots()[0] / "Profiles" / "smoke.default-release"
CURSOR = HOME / ".cursor" / "mcp.json"
APP = (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Agent F" if OS.name == "windows"
       else data_dir() / "app")
PYTHON = APP / "runtime" / ("python.exe" if OS.name == "windows" else "bin/python3")
FIREFOX = {"windows": [Path(os.environ.get("ProgramFiles", "")) / "Mozilla Firefox" / "firefox.exe"],
           "macos": [Path("/Applications/Firefox.app/Contents/MacOS/firefox")]}[OS.name]


def check(condition: bool, what: str) -> None:
    print(("ok   " if condition else "FAIL ") + what, flush=True)
    if not condition:
        log = data_dir() / "logs" / "install.log"
        if log.exists():
            print("\n--- install.log ---\n" + log.read_text(encoding="utf-8", errors="replace"))
        raise SystemExit(1)


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def prepare() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    (PROFILE.parents[1] / "profiles.ini").write_text(
        "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/smoke.default-release\n\n"
        "[Install0000000000000000]\nDefault=Profiles/smoke.default-release\nLocked=1\n\n"
        "[General]\nStartWithLastProfile=1\nVersion=2\n", encoding="utf-8")
    # Enable add-ons put into the profile folder without asking, as a person would from the menu notice.
    (PROFILE / "user.js").write_text("\n".join([
        'user_pref("extensions.autoDisableScopes", 0);',
        'user_pref("browser.shell.checkDefaultBrowser", false);',
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);',
        'user_pref("browser.aboutwelcome.enabled", false);',
        'user_pref("app.update.disabledForTesting", true);',
    ]) + "\n", encoding="utf-8")
    CURSOR.parent.mkdir(parents=True, exist_ok=True)
    CURSOR.write_text(json.dumps({"mcpServers": {"other": {"url": "http://example.invalid/mcp"}}}), encoding="utf-8")


def install(installer: Path) -> None:
    if OS.name == "windows":
        log = REPO / "dist" / "setup.log"
        subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"],
                       check=True, timeout=600)
    else:
        subprocess.run(["installer", "-pkg", str(installer), "-target", "CurrentUserHomeDirectory", "-verbose"],
                       check=True, timeout=600)


def native_host_registered() -> bool:
    if OS.name == "windows":
        import winreg
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, OS._host_key(NATIVE_HOST_NAME)))
            return True
        except FileNotFoundError:
            return False
    return any((d / f"{NATIVE_HOST_NAME}.json").exists() for d in OS.native_manifest_dirs())


def config() -> dict:
    return json.loads((data_dir() / "config.json").read_text(encoding="utf-8"))


def check_installed(label: str) -> None:
    print(f"\n== {label}")
    check(PYTHON.exists(), f"bundled Python at {PYTHON}")
    check(json.loads((APP / "bundle.json").read_text(encoding="utf-8"))["platform"] == OS.name, "bundle.json")
    check(not (data_dir() / "native" / platforms.PAUSE_MARKER).exists(), "no pause marker left")
    deadline = time.time() + 30
    while not listening(config()["mcp_port"]) and time.time() < deadline:
        time.sleep(0.5)
    check(listening(config()["mcp_port"]), f"broker listening on {config()['mcp_port']}")
    check(Path(config()["broker_command"][0]).parent == PYTHON.parent, "broker runs the bundled Python")
    check(native_host_registered(), "native-messaging helper registered")
    check((PROFILE / "extensions" / f"{ADDON_ID}.xpi").exists(), "add-on in the default profile")
    servers = json.loads(CURSOR.read_text(encoding="utf-8"))["mcpServers"]
    check("agent-f" in servers and "other" in servers, "Cursor entry added, other entries kept")
    if OS.name == "macos":
        check((HOME / "Applications" / "Uninstall Agent F.app").exists(), "Uninstall Agent F in ~/Applications")


def browsers() -> str:
    result = subprocess.run([str(PYTHON), "-m", "agent_f.selftest"], capture_output=True, text=True, timeout=60)
    return result.stdout.strip()


def wait_for_firefox(label: str) -> None:
    deadline = time.time() + 120
    seen = ""
    while time.time() < deadline:
        seen = browsers()
        if "browser connected" in seen:
            break
        time.sleep(2)
    found = next((line for line in seen.splitlines() if "browser connected" in line), seen[-300:])
    check("browser connected" in seen, f"{label}: Firefox connected through the helper ({found.strip()})")


def uninstall() -> None:
    print("\n== uninstall")
    if OS.name == "windows":
        subprocess.run([str(APP / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/purge=1",
                        f"/LOG={REPO / 'dist' / 'uninstall.log'}"], check=True, timeout=300)
        deadline = time.time() + 60
        while APP.exists() and time.time() < deadline:
            time.sleep(1)
    else:
        subprocess.run([str(PYTHON), str(APP / "install" / "install.py"), "--uninstall", "--purge"],
                       check=True, timeout=300)
    check(not APP.exists(), f"{APP} removed")
    check(not data_dir().exists(), "data folder removed")
    check(not native_host_registered(), "native-messaging helper unregistered")
    check(not listening(47470), "broker stopped")
    check("agent-f" not in json.loads(CURSOR.read_text(encoding="utf-8"))["mcpServers"], "Cursor entry removed")
    if OS.name == "windows":
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{5E0C9B54-4A57-4C1E-9F3B-AF52A7B8E1D3}_is1"
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, key))
            gone = False
        except FileNotFoundError:
            gone = True
        check(gone, "removed from Settings > Apps")
    else:
        check(not (HOME / "Applications" / "Uninstall Agent F.app").exists(), "Uninstall Agent F removed")
        check(not (HOME / "Library" / "LaunchAgents" / f"{platforms.LAUNCH_AGENT}.plist").exists(),
              "launch agent removed")


def main() -> None:
    if not os.environ.get("CI"):
        raise SystemExit("This test rewrites Firefox's profiles.ini and installs Agent F; it only runs in CI.")
    installer = Path(sys.argv[2]).resolve()
    prepare()
    install(installer)
    check_installed("first install")

    firefox = next((p for p in FIREFOX if p.exists()), None)
    check(firefox is not None, "Firefox is installed on this machine")
    process = subprocess.Popen([str(firefox), "-headless", "-no-remote", "-profile", str(PROFILE), "about:blank"])
    try:
        wait_for_firefox("first install")
        install(installer)
        check_installed("installed again over a running Firefox")
        wait_for_firefox("after the update")
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
    time.sleep(3)
    uninstall()
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
