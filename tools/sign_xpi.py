"""Sign an Agent F build as an unlisted (self-distributed) add-on on addons.mozilla.org.

    py -3 tools/sign_xpi.py dist/agent-f-0.4.0.xpi

Credentials come from AMO_JWT_ISSUER and AMO_JWT_SECRET, or from .local/amo-credentials.json in this
clone ({"issuer": "...", "secret": "..."}; the .local folder is git-ignored). Create them at
https://addons.mozilla.org/developers/addon/api/key/. Standard library only.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
API = "https://addons.mozilla.org/api/v5"
CREDENTIALS = REPO / ".local" / "amo-credentials.json"


def credentials() -> tuple[str, str]:
    issuer, secret = os.environ.get("AMO_JWT_ISSUER"), os.environ.get("AMO_JWT_SECRET")
    if not (issuer and secret) and CREDENTIALS.exists():
        data = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
        issuer, secret = data.get("issuer"), data.get("secret")
    if not issuer or not secret or "paste" in issuer.lower():
        raise SystemExit(f"No addons.mozilla.org API key. Put it in {CREDENTIALS} or set AMO_JWT_ISSUER and AMO_JWT_SECRET.")
    return issuer.strip(), secret.strip()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def jwt(issuer: str, secret: str) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({"iss": issuer, "jti": secrets.token_hex(16), "iat": now, "exp": now + 240}).encode())
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


def request(method: str, url: str, creds, body: bytes | None = None, content_type: str | None = None,
            raw: bool = False):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"JWT {jwt(*creds)}")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
            return data if raw else json.loads(data or b"{}")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {url} failed with {err.code}: {detail}") from None


def multipart(fields: dict, file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = secrets.token_hex(16)
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'
        f"Content-Type: application/x-xpinstall\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def manifest_of(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("manifest.json"))


def sign(path: Path) -> Path:
    creds = credentials()
    manifest = manifest_of(path)
    guid = manifest["browser_specific_settings"]["gecko"]["id"]
    version = manifest["version"]
    quoted = urllib.parse.quote(guid, safe="")

    print(f"Uploading {path.name} ({guid} {version}) for unlisted signing...")
    body, ctype = multipart({"channel": "unlisted"}, "upload", path)
    upload = request("POST", f"{API}/addons/upload/", creds, body, ctype)
    uuid = upload["uuid"]
    while not upload.get("processed"):
        time.sleep(3)
        upload = request("GET", f"{API}/addons/upload/{uuid}/", creds)
    if not upload.get("valid"):
        messages = (upload.get("validation") or {}).get("messages", [])
        errors = [m for m in messages if m.get("type") == "error"] or messages
        for m in errors[:20]:
            print(f"  {m.get('type')}: {m.get('message')} ({m.get('file', '')})")
        raise SystemExit("addons.mozilla.org rejected the upload.")
    warnings = [m for m in (upload.get("validation") or {}).get("messages", []) if m.get("type") == "warning"]
    if warnings:
        print(f"  {len(warnings)} validation warnings (fine for unlisted add-ons).")

    # Unlisted versions need no licence or listing metadata.
    payload = json.dumps({"upload": uuid}).encode()
    try:
        request("POST", f"{API}/addons/addon/{quoted}/versions/", creds, payload, "application/json")
    except RuntimeError as err:
        if " 404:" not in str(err):
            raise
        print("First upload of this add-on; creating it.")
        create = json.dumps({"version": {"upload": uuid}}).encode()
        request("PUT", f"{API}/addons/addon/{quoted}/", creds, create, "application/json")

    print("Waiting for addons.mozilla.org to sign it...")
    for _ in range(120):
        detail = request("GET", f"{API}/addons/addon/{quoted}/versions/{urllib.parse.quote(version)}/", creds)
        file_info = detail.get("file") or {}
        if file_info.get("status") == "public" and file_info.get("url"):
            signed = path.with_name(f"agent-f-{version}-signed.xpi")
            signed.write_bytes(request("GET", file_info["url"], creds, raw=True))
            print(f"Signed: {signed}")
            return signed
        if file_info.get("status") == "disabled":
            raise SystemExit("The version was disabled on addons.mozilla.org; check the developer hub.")
        time.sleep(5)
    raise SystemExit("Timed out waiting for signing; check the developer hub, then rerun to download.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sign(Path(sys.argv[1]))
