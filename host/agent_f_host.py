"""Agent F native-messaging helper.

Firefox starts this once per profile. It relays messages unchanged between Firefox
(stdio, native byte order length prefix) and the Agent F broker (TCP, big-endian
length prefix), after authenticating to the broker with the shared token.
Standard library only.
"""

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

CONNECT_ATTEMPTS = 20
CONNECT_DELAY = 0.5


def data_dir() -> Path:
    override = os.environ.get("AGENT_F_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "agent-f"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "agent-f"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "agent-f"


def log(message: str) -> None:
    try:
        logs = data_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "helper.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {message}\n")
    except OSError:
        pass


def read_native(stream) -> bytes | None:
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("=I", header)
    data = stream.read(length)
    return data if len(data) == length else None


def write_native(stream, data: bytes) -> None:
    stream.write(struct.pack("=I", len(data)))
    stream.write(data)
    stream.flush()


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    chunks = []
    while n:
        chunk = sock.recv(min(n, 1 << 20))
        if not chunk:
            return None
        chunks.append(chunk)
        n -= len(chunk)
    return b"".join(chunks)


def send_frame(sock: socket.socket, data: bytes) -> None:
    sock.sendall(struct.pack(">I", len(data)) + data)


def start_broker(command: list[str]) -> None:
    if not command:
        return
    detach = {"start_new_session": True}
    if sys.platform == "win32":
        detach = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                  | subprocess.CREATE_NO_WINDOW}
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, **detach)
        log("started broker")
    except OSError as err:
        log(f"could not start broker: {err}")


def connect(cfg: dict) -> socket.socket | None:
    address = (cfg.get("host", "127.0.0.1"), int(cfg.get("bridge_port", 47471)))
    started = False
    for _ in range(CONNECT_ATTEMPTS):
        try:
            return socket.create_connection(address, timeout=3)
        except OSError:
            if not started:
                start_broker(cfg.get("broker_command") or [])
                started = True
            time.sleep(CONNECT_DELAY)
    return None


def main() -> int:
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer

    try:
        cfg = json.loads((data_dir() / "config.json").read_text(encoding="utf-8"))
        token = (data_dir() / "token").read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as err:
        log(f"not installed correctly: {err}")
        return 1

    sock = connect(cfg)
    if sock is None:
        log("broker unreachable")
        return 1
    sock.settimeout(None)
    send_frame(sock, json.dumps({"type": "auth", "token": token}).encode("utf-8"))
    log("connected to broker")

    def broker_to_firefox():
        try:
            while True:
                header = recv_exact(sock, 4)
                if header is None:
                    break
                (length,) = struct.unpack(">I", header)
                data = recv_exact(sock, length)
                if data is None:
                    break
                write_native(stdout, data)
        except OSError:
            pass
        log("broker connection closed")
        os._exit(0)

    threading.Thread(target=broker_to_firefox, daemon=True).start()

    try:
        while True:
            data = read_native(stdin)
            if data is None:
                break
            send_frame(sock, data)
    except OSError:
        pass
    log("Firefox closed the connection")
    try:
        sock.close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
