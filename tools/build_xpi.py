"""Build the Agent F add-on into dist/agent-f-<version>.xpi (an unsigned zip of extension/).

    py -3 tools/build_xpi.py
"""

import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXTENSION = REPO / "extension"
DIST = REPO / "dist"
# Fixed timestamps make the archive depend only on the files' contents.
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def version() -> str:
    return json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))["version"]


def build() -> Path:
    DIST.mkdir(exist_ok=True)
    out = DIST / f"agent-f-{version()}.xpi"
    files = sorted(p for p in EXTENSION.rglob("*") if p.is_file())
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(EXTENSION).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    return out


if __name__ == "__main__":
    path = build()
    print(path)
    sys.exit(0)
