import plistlib
import sys
from pathlib import Path, PurePosixPath

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "install"))

import install  # noqa: E402
import platforms  # noqa: E402

COMMAND = ["/home/me/.local/share/agent-f/venv/bin/python", "-m", "agent_f.broker"]


def test_venv_python_paths():
    venv = Path("/v")
    assert platforms.Windows().venv_python(venv, windowed=True) == venv / "Scripts" / "pythonw.exe"
    assert platforms.Windows().venv_python(venv) == venv / "Scripts" / "python.exe"
    assert platforms.MacOS().venv_python(venv, windowed=True) == venv / "bin" / "python"
    assert platforms.Linux().venv_python(venv) == venv / "bin" / "python"


def test_native_manifest_locations(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "HOME", tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    assert platforms.MacOS().native_manifest_dirs() == [
        tmp_path / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts"]
    assert platforms.Linux().native_manifest_dirs() == [tmp_path / ".mozilla" / "native-messaging-hosts"]
    (tmp_path / ".config" / "mozilla").mkdir(parents=True)
    assert platforms.Linux().native_manifest_dirs()[1] == tmp_path / ".config" / "mozilla" / "native-messaging-hosts"


def test_register_and_unregister_on_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(platforms, "HOME", tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    manifest = tmp_path / "agent_f.json"
    manifest.write_text("{}", encoding="utf-8")
    linux = platforms.Linux()
    written = linux.register_native_host(manifest, "agent_f")
    assert written == [tmp_path / ".mozilla" / "native-messaging-hosts" / "agent_f.json"]
    assert written[0].read_text(encoding="utf-8") == "{}"
    assert linux.unregister_native_host("agent_f")
    assert not written[0].exists()


def test_posix_launcher_quotes_paths(tmp_path):
    launcher = platforms.Linux().write_launcher(tmp_path, PurePosixPath("/a b/python"),
                                                PurePosixPath("/c/agent_f_host.py"))
    text = launcher.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "exec '/a b/python' /c/agent_f_host.py \"$@\"" in text


def test_windows_launcher_waits_out_an_install(tmp_path):
    launcher = platforms.Windows().write_launcher(tmp_path, Path("C:/A/runtime/python.exe"),
                                                  Path("C:/D/agent_f_host.py"))
    lines = launcher.read_bytes().decode("utf-8").split("\r\n")
    assert lines[1] == f'if exist "%~dp0{platforms.PAUSE_MARKER}" exit /b 1'
    assert lines[2] == f'"{Path("C:/A/runtime/python.exe")}" "{Path("C:/D/agent_f_host.py")}" %*'


def test_launch_agent_plist():
    data = plistlib.loads(platforms.MacOS().launch_agent(COMMAND))
    assert data["Label"] == platforms.LAUNCH_AGENT
    assert data["ProgramArguments"] == COMMAND
    assert data["RunAtLoad"] is True


def test_systemd_unit_and_desktop_entry():
    unit = platforms.Linux().systemd_unit(COMMAND)
    assert f"ExecStart={' '.join(COMMAND)}" in unit
    assert "WantedBy=default.target" in unit
    assert f"Exec={' '.join(COMMAND)}" in platforms.Linux().desktop_entry(COMMAND)


def test_default_port_is_usable_everywhere():
    for os_ in (platforms.MacOS(), platforms.Linux()):
        assert not any(lo <= 47470 <= hi for lo, hi in os_.reserved_port_ranges())


@pytest.fixture
def firefox_root(monkeypatch, tmp_path):
    root = tmp_path / "Firefox"
    root.mkdir()
    (root / "profiles.ini").write_text(
        "[Profile1]\nName=default\nIsRelative=1\nPath=Profiles/abc.default\nDefault=1\n\n"
        "[Profile0]\nName=default-nightly\nIsRelative=1\nPath=Profiles/xyz.default-nightly\n\n"
        f"[Profile2]\nName=elsewhere\nIsRelative=0\nPath={tmp_path / 'other'}\n\n"
        "[Install308046B0AF4A39CB]\nDefault=Profiles/xyz.default-nightly\nLocked=1\n",
        encoding="utf-8")
    monkeypatch.setattr(install.OS, "firefox_roots", lambda: [root, tmp_path / "missing"])
    return root


def test_firefox_profiles_marks_install_defaults(firefox_root, tmp_path):
    profiles = {p["name"]: p for p in install.firefox_profiles()}
    assert set(profiles) == {"default", "default-nightly", "elsewhere"}
    assert profiles["default-nightly"]["default"] is True
    assert profiles["default"]["default"] is False
    assert profiles["default-nightly"]["path"] == firefox_root / "Profiles/xyz.default-nightly"
    assert profiles["elsewhere"]["path"] == tmp_path / "other"


def test_install_addon_skips_source_linked_profiles(firefox_root, monkeypatch, tmp_path):
    xpi = tmp_path / "agent-f.xpi"
    xpi.write_bytes(b"signed")
    monkeypatch.setattr(install, "download_signed_addon", lambda: xpi)
    linked = firefox_root / "Profiles/xyz.default-nightly/extensions"
    linked.mkdir(parents=True)
    (linked / install.ADDON_ID).write_text(str(tmp_path), encoding="utf-8")
    install.install_addon(["default", "default-nightly"])
    assert (firefox_root / "Profiles/abc.default/extensions" / f"{install.ADDON_ID}.xpi").read_bytes() == b"signed"
    assert not (linked / f"{install.ADDON_ID}.xpi").exists()


def test_install_addon_rejects_unknown_profile(firefox_root):
    with pytest.raises(SystemExit, match="nope"):
        install.install_addon(["nope"])


def test_install_addon_keeps_an_installed_copy(firefox_root, monkeypatch, tmp_path):
    installed = firefox_root / "Profiles/xyz.default-nightly/extensions"
    installed.mkdir(parents=True)
    (installed / f"{install.ADDON_ID}.xpi").write_bytes(b"newer, from an update")

    def no_download():
        raise AssertionError("nothing to download")

    monkeypatch.setattr(install, "download_signed_addon", no_download)
    install.install_addon([])
    assert (installed / f"{install.ADDON_ID}.xpi").read_bytes() == b"newer, from an update"


def test_install_addon_uses_the_bundled_copy(firefox_root, monkeypatch, tmp_path):
    xpi = tmp_path / "bundled.xpi"
    xpi.write_bytes(b"bundled")
    monkeypatch.setattr(install, "download_signed_addon", lambda: pytest.fail("should use the bundled add-on"))
    install.install_addon([], xpi)
    target = firefox_root / "Profiles/xyz.default-nightly/extensions" / f"{install.ADDON_ID}.xpi"
    assert target.read_bytes() == b"bundled"


def test_bundled_python_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "executable", str(Path("C:/A/0.5.0/runtime/python.exe")))
    assert platforms.Windows().bundled_python(windowed=True) == Path("C:/A/0.5.0/runtime/pythonw.exe")
    assert platforms.Windows().bundled_python() == Path("C:/A/0.5.0/runtime/python.exe")


def test_uninstall_blanks_the_broker_command_before_stopping(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_F_HOME", str(tmp_path))
    install.save_config(install.Config(broker_command=["python", "-m", "agent_f.broker"]))
    order = []
    monkeypatch.setattr(install.OS, "unregister_native_host", lambda name: order.append("unregister") or True)
    monkeypatch.setattr(install.OS, "remove_autostart", lambda: order.append("autostart") or True)
    monkeypatch.setattr(install, "stop_broker",
                        lambda: order.append(("stop", install.load_config().broker_command)))
    monkeypatch.setattr(install, "remove_cursor_config", lambda: None)
    monkeypatch.setattr(install, "BUNDLE", None)
    install.uninstall(type("Args", (), {"purge": False})())
    assert order == ["unregister", "autostart", ("stop", [])]
