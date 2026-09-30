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
