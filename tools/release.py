"""Publish a signed Agent F release: build, sign, attach to a GitHub release, update the update manifest.

    py -3 tools/release.py

Needs an addons.mozilla.org API key (see sign_xpi.py) and the GitHub CLI signed in with push access.
Firefox installs with Agent F check docs/updates.json, served by GitHub Pages at the manifest's
update_url, and update themselves. Publishing the release starts the Installers workflow, which builds the
Windows and macOS installers from the tagged commit around the signed add-on, tests them, and attaches them.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import build_xpi  # noqa: E402
import sign_xpi  # noqa: E402

GITHUB_REPO = "drubino-mozilla/agent-f"
UPDATES = REPO / "docs" / "updates.json"


def run(*args: str) -> str:
    return subprocess.run(args, cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    if run("git", "status", "--porcelain"):
        raise SystemExit("Commit or stash your changes first; a release is built from a clean tree.")
    manifest = json.loads((REPO / "extension" / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    gecko = manifest["browser_specific_settings"]["gecko"]
    addon_id = gecko["id"]
    tag = f"v{version}"

    unsigned = build_xpi.build()
    signed = sign_xpi.sign(unsigned)
    asset = signed.with_name(f"agent-f-{version}.xpi")
    asset.write_bytes(signed.read_bytes())
    sha256 = hashlib.sha256(asset.read_bytes()).hexdigest()
    link = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{asset.name}"

    # The installers are built from the tagged commit, so it has to be on GitHub before the tag is.
    run("git", "push")
    notes = (f"Agent F {version}. To install or update, run `Agent-F-Windows.exe` or `Agent-F-macOS.pkg`; they "
             f"are attached a few minutes after the release is published. `{asset.name}` is the signed Firefox "
             "add-on on its own.")
    run("gh", "release", "create", tag, str(asset), "--repo", GITHUB_REPO, "--title", f"Agent F {version}",
        "--target", run("git", "rev-parse", "HEAD"), "--notes", notes)

    updates = json.loads(UPDATES.read_text(encoding="utf-8")) if UPDATES.exists() else {"addons": {}}
    entries = updates.setdefault("addons", {}).setdefault(addon_id, {}).setdefault("updates", [])
    entries[:] = [e for e in entries if e.get("version") != version]
    entries.append({
        "version": version,
        "update_link": link,
        "update_hash": f"sha256:{sha256}",
        "applications": {"gecko": {"strict_min_version": gecko.get("strict_min_version", "140.0")}},
    })
    UPDATES.parent.mkdir(exist_ok=True)
    UPDATES.write_text(json.dumps(updates, indent=2) + "\n", encoding="utf-8")
    run("git", "add", str(UPDATES))
    run("git", "commit", "-m", f"Update manifest for {version}")
    run("git", "push")
    print(f"Released {tag}: {link}")
    print(f"The installers follow from the Installers workflow: https://github.com/{GITHUB_REPO}/actions")


if __name__ == "__main__":
    main()
