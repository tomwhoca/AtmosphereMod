"""Evaluate JavaScript in the running sim's UI (its debugger at 127.0.0.1:19999: Dev Mode, the Coherent debugger
on). For the EFB app: tools/efb_reload.py reloads its UI and checks its layout with it.

    python tools/coherent.py --list                      # the live UI views
    python tools/coherent.py "Electronic Flight Bag" "document.title"
    python tools/coherent.py 7 --file probe.js           # by id; the file's last expression is the answer

The debugger speaks a WebKit inspector protocol over a WebSocket: Runtime.evaluate with returnByValue works, which
is all this needs. No screenshots: return what you want to know as a value (JSON.stringify for objects).
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request

HOST, PORT = "127.0.0.1", 19999
TABLET = "Electronic Flight Bag"  # the sim's own tablet, where the EFB app runs


def pages() -> list[dict]:
    with urllib.request.urlopen(f"http://{HOST}:{PORT}/pagelist.json", timeout=6) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def page_id(name_or_id: str) -> str:
    """A page's id: given as one, or the first whose title starts with the name."""
    if name_or_id.isdigit():
        return name_or_id
    for p in pages():
        if str(p.get("title", "")).startswith(name_or_id):
            return str(p["id"])
    raise SystemExit(f"no UI view titled {name_or_id!r} (is the sim running, with the Coherent debugger on?)")


def _connect(path: str) -> tuple[socket.socket, bytes]:
    s = socket.create_connection((HOST, PORT), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            raise OSError("closed during the handshake")
        data += chunk
    head, rest = data.split(b"\r\n\r\n", 1)
    if b" 101 " not in head.split(b"\r\n", 1)[0] + b" ":
        s.close()
        raise OSError(head.split(b"\r\n", 1)[0].decode("ascii", "replace"))
    return s, rest


def _send(s: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    n = len(payload)
    head = bytearray([0x81])
    head += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 \
        else bytes([0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)
    head += mask
    s.sendall(bytes(head) + bytes(b ^ mask[i & 3] for i, b in enumerate(payload)))


def _receive(s: socket.socket, buffer: bytes) -> tuple[str, bytes]:
    buf = bytearray(buffer)

    def fill(n: int) -> None:
        while len(buf) < n:
            chunk = s.recv(65536)
            if not chunk:
                raise OSError("closed")
            buf.extend(chunk)
    fill(2)
    n, at = buf[1] & 0x7F, 2
    if n == 126:
        fill(4)
        n, at = struct.unpack(">H", bytes(buf[2:4]))[0], 4
    elif n == 127:
        fill(10)
        n, at = struct.unpack(">Q", bytes(buf[2:10]))[0], 10
    fill(at + n)
    return bytes(buf[at:at + n]).decode("utf-8", "replace"), bytes(buf[at + n:])


def evaluate(page: str, expression: str, timeout: float = 20.0):
    """The expression's value in the page (by id or title), or raises with what the page threw."""
    pid = page_id(page)
    last = None
    for shape in ("/devtools/page/%s", "/inspector/page/%s", "/devtools/%s", "/%s"):
        try:
            s, rest = _connect(shape % pid)
        except OSError as e:
            last = e
            continue
        try:
            _send(s, json.dumps({"id": 1, "method": "Runtime.evaluate",
                                 "params": {"expression": expression, "returnByValue": True}}))
            end = time.time() + timeout
            while time.time() < end:
                text, rest = _receive(s, rest)
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if msg.get("id") != 1:
                    continue
                result = msg.get("result", {})
                if result.get("wasThrown") or result.get("exceptionDetails"):
                    raise RuntimeError(f"the page threw: {json.dumps(result)[:2000]}")
                inner = result.get("result", result)
                return inner.get("value", inner)
            raise TimeoutError("no answer from the page")
        finally:
            s.close()
    raise OSError(f"the debugger took no connection: {last}")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--list":
        for p in pages():
            print(f"{p.get('id'):>4}  {p.get('title')}")
        return 0
    expr = open(argv[2], encoding="utf-8").read() if len(argv) > 2 and argv[1] == "--file" else \
        (argv[1] if len(argv) > 1 else "1+1")
    value = evaluate(argv[0], expr)
    print(json.dumps(value, indent=2) if isinstance(value, (dict, list)) else value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
