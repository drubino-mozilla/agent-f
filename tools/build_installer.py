"""Build the Windows or macOS installer from this checkout.

    python tools/build_installer.py windows --xpi agent-f-0.4.3.xpi   writes dist/Agent-F-Windows.exe
    python tools/build_installer.py macos --xpi agent-f-0.4.3.xpi     writes dist/Agent-F-macOS.pkg

Run it on the platform it builds for, with the Python version in packaging/runtimes.json; the Installers
workflow (.github/workflows/installers.yml) does both. It downloads and checks the pinned runtimes, installs
the broker and its locked dependencies (packaging/requirements-<os>.txt) into them, lays out the folder that
install/install.py runs from in bundled mode, and packages it with Inno Setup or pkgbuild and productbuild.
The version comes from extension/manifest.json, so an installer always matches the add-on it carries.
"""

import argparse
import compileall
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import lock_bundle  # noqa: E402

PACKAGING = REPO / "packaging"
BUILD = REPO / "build" / "installer"
CACHE = REPO / "build" / "cache"
DIST = REPO / "dist"
RUNTIMES = lock_bundle.RUNTIMES
PY = RUNTIMES["python"]
PKG_ID = "io.github.drubino-mozilla.agent-f"
# Inside the user's home folder: the macOS package installs "for me only", like the Windows one.
MAC_INSTALL_LOCATION = "/Library/Application Support/agent-f/app"


def run(*args, **kwargs) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(runtime: str) -> Path:
    spec = RUNTIMES[runtime]
    path = CACHE / urllib.parse.unquote(spec["url"].rsplit("/", 1)[1])
    if not path.exists() or sha256(path) != spec["sha256"]:
        CACHE.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(spec["url"], timeout=300) as resp, open(partial, "wb") as out:
            shutil.copyfileobj(resp, out)
        if sha256(partial) != spec["sha256"]:
            partial.unlink()
            raise SystemExit(f"{spec['url']} doesn't match its pinned SHA-256 in packaging/runtimes.json")
        partial.replace(path)
    return path


def stage_common(stage: Path, version: str, platform: str, xpi: Path) -> None:
    if stage.exists():
        shutil.rmtree(stage)
    for folder, names in (("install", ["install.py", "platforms.py"]), ("host", ["agent_f_host.py"])):
        (stage / folder).mkdir(parents=True)
        for name in names:
            shutil.copyfile(REPO / folder / name, stage / folder / name)
    (stage / "addon").mkdir()
    shutil.copyfile(xpi, stage / "addon" / "agent-f.xpi")
    (stage / "bundle.json").write_text(json.dumps({"version": version, "platform": platform}, indent=2) + "\n",
                                       encoding="utf-8")


def add_broker(site: Path, runtime: str, os_name: str, version: str) -> None:
    lock = lock_bundle.lock_path(runtime)
    if not lock.exists():
        raise SystemExit(f"{lock.name} is missing; run python tools/lock_bundle.py {os_name} first")
    run(sys.executable, "-m", "pip", "install", "--require-hashes", "--no-compile", "--quiet",
        "--target", site, *lock_bundle.pip_target_args(runtime), "-r", lock)
    shutil.rmtree(site / "bin", ignore_errors=True)
    shutil.copytree(REPO / "broker" / "agent_f", site / "agent_f", ignore=shutil.ignore_patterns("__pycache__"))
    init = site / "agent_f" / "__init__.py"
    init.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{version}"', init.read_text(encoding="utf-8")),
                    encoding="utf-8")
    if f"{sys.version_info[0]}.{sys.version_info[1]}" == PY:
        compileall.compile_dir(site, quiet=1, workers=0)


def find_iscc() -> Path:
    candidates = [os.environ.get("ISCC"), shutil.which("iscc"),
                  Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit("Inno Setup 6 isn't installed (https://jrsoftware.org/isinfo.php, or choco install innosetup)")


def build_windows(version: str, xpi: Path) -> Path:
    stage = BUILD / "windows"
    stage_common(stage, version, "windows", xpi)
    runtime = stage / "runtime"
    with zipfile.ZipFile(fetch("windows")) as z:
        z.extractall(runtime)
    # The embeddable Python takes its whole sys.path from this file. "import site" makes it process the
    # .pth files in site-packages, which pywin32 (an mcp dependency on Windows) needs.
    pth = next(runtime.glob("python3*._pth"))
    zipped = next(runtime.glob("python3*.zip")).name
    pth.write_text(f"{zipped}\n.\nLib\\site-packages\nimport site\n", encoding="utf-8")
    add_broker(runtime / "Lib" / "site-packages", "windows", "windows", version)
    DIST.mkdir(exist_ok=True)
    run(find_iscc(), "/Q", f"/DAppVersion={version}", f"/DStage={stage}", f"/DIcon={REPO / 'assets' / 'red-panda.ico'}",
        f"/DOutputDir={DIST}", PACKAGING / "windows" / "agent-f.iss")
    return DIST / "Agent-F-Windows.exe"


def slim(runtime: Path) -> None:
    """Drop the parts of a standalone Python that Agent F never uses."""
    lib = runtime / "lib" / f"python{PY}"
    for path in [runtime / "include", runtime / "share", *runtime.glob("lib/libtcl*"), *runtime.glob("lib/libtk*"),
                 *runtime.glob("lib/tcl*"), *runtime.glob("lib/tk*"), *runtime.glob("lib/itcl*"),
                 *runtime.glob("lib/thread*"), lib / "test", lib / "idlelib", lib / "tkinter", lib / "turtledemo",
                 lib / "ensurepip", *lib.glob("lib-dynload/_tkinter*"), *lib.glob("site-packages/pip*")]:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()


def make_icns(dest: Path) -> None:
    iconset = BUILD / "macos" / "agent-f.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            run("sips", "-z", px, px, REPO / "assets" / "red-panda-master.png", "--out", iconset / name,
                stdout=subprocess.DEVNULL)
    run("iconutil", "-c", "icns", iconset, "-o", dest)


def build_macos(version: str, xpi: Path) -> Path:
    work = BUILD / "macos"
    stage = work / "root"
    stage_common(stage, version, "macos", xpi)
    for arch in ("arm64", "x86_64"):
        unpacked = work / f"unpack-{arch}"
        shutil.rmtree(unpacked, ignore_errors=True)
        with tarfile.open(fetch(f"macos-{arch}")) as tar:
            tar.extractall(unpacked, filter="data")
        runtime = stage / f"runtime-{arch}"
        (unpacked / "python").rename(runtime)
        slim(runtime)
        add_broker(runtime / "lib" / f"python{PY}" / "site-packages", f"macos-{arch}", "macos", version)
    shutil.copyfile(PACKAGING / "macos" / "uninstall.applescript", stage / "uninstall.applescript")
    make_icns(stage / "agent-f.icns")

    scripts = work / "scripts"
    shutil.rmtree(scripts, ignore_errors=True)
    scripts.mkdir()
    for name in ("preinstall", "postinstall"):
        shutil.copyfile(PACKAGING / "macos" / "scripts" / name, scripts / name)
        (scripts / name).chmod(0o755)
    component = work / "agent-f-component.pkg"
    run("pkgbuild", "--root", stage, "--identifier", PKG_ID, "--version", version,
        "--install-location", MAC_INSTALL_LOCATION, "--scripts", scripts, component)
    distribution = work / "distribution.xml"
    distribution.write_text((PACKAGING / "macos" / "distribution.xml").read_text(encoding="utf-8")
                            .replace("@VERSION@", version).replace("@PKG_ID@", PKG_ID), encoding="utf-8")
    DIST.mkdir(exist_ok=True)
    output = DIST / "Agent-F-macOS.pkg"
    run("productbuild", "--distribution", distribution, "--resources", PACKAGING / "macos" / "resources",
        "--package-path", work, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Agent F installer")
    parser.add_argument("os", choices=["windows", "macos"])
    parser.add_argument("--xpi", type=Path, required=True, help="the signed add-on to bundle")
    args = parser.parse_args()
    version = json.loads((REPO / "extension" / "manifest.json").read_text(encoding="utf-8"))["version"]
    output = (build_windows if args.os == "windows" else build_macos)(version, args.xpi.resolve())
    print(f"built {output} ({output.stat().st_size // 1_000_000} MB), Agent F {version}")


if __name__ == "__main__":
    main()
