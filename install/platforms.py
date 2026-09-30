"""What the installer does differently on Windows, macOS and Linux. Standard library only."""

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
LAUNCH_AGENT = "io.github.drubino-mozilla.agent-f"
SYSTEMD_UNIT = "agent-f-broker.service"


class Platform:
    name = ""

    def venv_python(self, venv: Path, windowed: bool = False) -> Path:
        return venv / "bin" / "python"

    def write_launcher(self, native_dir: Path, python: Path, host_script: Path) -> Path:
        launcher = native_dir / "agent_f_host.sh"
        launcher.write_text(f"#!/bin/sh\nexec {shlex.quote(str(python))} {shlex.quote(str(host_script))} \"$@\"\n",
                            encoding="utf-8")
        launcher.chmod(0o755)
        return launcher

    def native_manifest_dirs(self) -> list[Path]:
        raise NotImplementedError

    def register_native_host(self, manifest: Path, host_name: str) -> list[Path]:
        written = []
        for folder in self.native_manifest_dirs():
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{host_name}.json"
            shutil.copyfile(manifest, target)
            written.append(target)
        return written

    def unregister_native_host(self, host_name: str) -> bool:
        removed = False
        for folder in self.native_manifest_dirs():
            target = folder / f"{host_name}.json"
            if target.exists():
                target.unlink()
                removed = True
        return removed

    def set_autostart(self, command: list[str]) -> str:
        raise NotImplementedError

    def remove_autostart(self) -> bool:
        raise NotImplementedError

    def is_python_process(self, pid: int) -> bool:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, check=False)
        return "python" in result.stdout.lower()

    def kill(self, pid: int) -> None:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    def start_detached(self, command: list[str]) -> None:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, start_new_session=True)

    def reserved_port_ranges(self) -> list[tuple[int, int]]:
        """Port ranges the installer must not pick: reserved ranges and the OS's ephemeral range."""
        return [(49152, 65535)]

    def firefox_roots(self) -> list[Path]:
        """Folders that can hold a Firefox profiles.ini."""
        raise NotImplementedError


class Windows(Platform):
    name = "windows"
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    RUN_VALUE = "Agent F broker"

    def venv_python(self, venv: Path, windowed: bool = False) -> Path:
        return venv / "Scripts" / ("pythonw.exe" if windowed else "python.exe")

    def write_launcher(self, native_dir: Path, python: Path, host_script: Path) -> Path:
        launcher = native_dir / "agent_f_host.bat"
        launcher.write_text(f'@echo off\r\n"{python}" "{host_script}" %*\r\n', encoding="utf-8")
        return launcher

    @staticmethod
    def _host_key(host_name: str) -> str:
        return rf"Software\Mozilla\NativeMessagingHosts\{host_name}"

    def register_native_host(self, manifest: Path, host_name: str) -> list[Path]:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._host_key(host_name)) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
        return [manifest]

    def unregister_native_host(self, host_name: str) -> bool:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self._host_key(host_name))
            return True
        except FileNotFoundError:
            return False

    def set_autostart(self, command: list[str]) -> str:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
            winreg.SetValueEx(key, self.RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        return "broker starts at logon"

    def remove_autostart(self) -> bool:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.RUN_VALUE)
            return True
        except FileNotFoundError:
            return False

    def is_python_process(self, pid: int) -> bool:
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, check=False).stdout
        return "python" in listing.lower()

    def kill(self, pid: int) -> None:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)

    def start_detached(self, command: list[str]) -> None:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, creationflags=flags)

    @staticmethod
    def _netsh(*args: str) -> str:
        try:
            return subprocess.run(["netsh", *args], capture_output=True, text=True, check=False).stdout
        except OSError:
            return ""

    def reserved_port_ranges(self) -> list[tuple[int, int]]:
        ranges = []
        for line in self._netsh("interface", "ipv4", "show", "excludedportrange", "protocol=tcp").splitlines():
            m = re.match(r"\s*(\d+)\s+(\d+)", line)
            if m:
                ranges.append((int(m.group(1)), int(m.group(2))))
        m = re.search(r"Start Port\s*:\s*(\d+)", self._netsh("interface", "ipv4", "show", "dynamicport", "tcp"))
        ranges.append((int(m.group(1)) if m else 49152, 65535))
        return ranges

    def firefox_roots(self) -> list[Path]:
        return [Path(os.environ["APPDATA"]) / "Mozilla" / "Firefox"]


class MacOS(Platform):
    name = "macos"

    def native_manifest_dirs(self) -> list[Path]:
        return [HOME / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts"]

    def _plist(self) -> Path:
        return HOME / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT}.plist"

    def launch_agent(self, command: list[str]) -> bytes:
        return plistlib.dumps({
            "Label": LAUNCH_AGENT,
            "ProgramArguments": command,
            "RunAtLoad": True,
            "ProcessType": "Background",
        })

    def set_autostart(self, command: list[str]) -> str:
        path = self._plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.launch_agent(command))
        # Loaded at the next login; the installer starts this session's broker itself.
        return f"broker starts at login ({path})"

    def remove_autostart(self) -> bool:
        path = self._plist()
        if not path.exists():
            return False
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True, check=False)
        path.unlink()
        return True

    def firefox_roots(self) -> list[Path]:
        return [HOME / "Library" / "Application Support" / "Firefox"]


class Linux(Platform):
    name = "linux"

    def _config_home(self) -> Path:
        return Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))

    def native_manifest_dirs(self) -> list[Path]:
        dirs = [HOME / ".mozilla" / "native-messaging-hosts"]
        if (self._config_home() / "mozilla").exists():
            dirs.append(self._config_home() / "mozilla" / "native-messaging-hosts")
        return dirs

    def _unit(self) -> Path:
        return self._config_home() / "systemd" / "user" / SYSTEMD_UNIT

    def _desktop_entry(self) -> Path:
        return self._config_home() / "autostart" / "agent-f-broker.desktop"

    def systemd_unit(self, command: list[str]) -> str:
        return ("[Unit]\nDescription=Agent F broker\n\n"
                f"[Service]\nExecStart={shlex.join(command)}\nRestart=on-failure\n\n"
                "[Install]\nWantedBy=default.target\n")

    def desktop_entry(self, command: list[str]) -> str:
        return ("[Desktop Entry]\nType=Application\nName=Agent F broker\n"
                f"Exec={shlex.join(command)}\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n")

    def set_autostart(self, command: list[str]) -> str:
        if shutil.which("systemctl") and subprocess.run(["systemctl", "--user", "is-system-running"],
                                                         capture_output=True, check=False).returncode in (0, 1):
            unit = self._unit()
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(self.systemd_unit(command), encoding="utf-8")
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
            # Enabled for the next login; the installer starts this session's broker itself.
            subprocess.run(["systemctl", "--user", "enable", SYSTEMD_UNIT], capture_output=True, check=False)
            return f"broker starts at login (systemd user unit {SYSTEMD_UNIT})"
        entry = self._desktop_entry()
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(self.desktop_entry(command), encoding="utf-8")
        return f"broker starts at login ({entry})"

    def remove_autostart(self) -> bool:
        removed = False
        if self._unit().exists():
            subprocess.run(["systemctl", "--user", "disable", SYSTEMD_UNIT], capture_output=True, check=False)
            self._unit().unlink()
            removed = True
        if self._desktop_entry().exists():
            self._desktop_entry().unlink()
            removed = True
        return removed

    def reserved_port_ranges(self) -> list[tuple[int, int]]:
        # The ephemeral range (usually 32768-60999) covers the default port, and nothing reserves
        # ports up front the way Windows does, so a free port is good enough.
        return []

    def firefox_roots(self) -> list[Path]:
        return [
            HOME / ".mozilla" / "firefox",
            self._config_home() / "mozilla" / "firefox",
            HOME / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
            HOME / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        ]


def current() -> Platform:
    if sys.platform == "win32":
        return Windows()
    if sys.platform == "darwin":
        return MacOS()
    return Linux()
