"""Build the Agent F add-on into dist/agent-f-<version>.xpi (an unsigned zip of extension/).

    py -3 tools/build_xpi.py
"""

import json
import re
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


def stamp(html: bytes, ver: str) -> bytes:
    """Set ?v=<version> on an extension page's local stylesheet, script and image links.

    Firefox keeps an extension's stylesheets cached across an update or reload until it restarts, so an
    updated page would otherwise get the old files. Source pages may already carry a ?v= of their own.
    """
    pattern = re.compile(rb'((?:href|src)=")([^":?#]+\.(?:css|js|png|svg))(?:\?v=[^"]*)?(")')
    return pattern.sub(lambda m: m.group(1) + m.group(2) + b"?v=" + ver.encode() + m.group(3), html)


def build() -> Path:
    DIST.mkdir(exist_ok=True)
    ver = version()
    out = DIST / f"agent-f-{ver}.xpi"
    files = sorted(p for p in EXTENSION.rglob("*") if p.is_file())
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(EXTENSION).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            data = path.read_bytes()
            z.writestr(info, stamp(data, ver) if path.suffix == ".html" else data)
    return out


if __name__ == "__main__":
    path = build()
    print(path)
    sys.exit(0)
