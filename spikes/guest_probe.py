"""Network probe that runs inside a VM (stdlib only, the image has no curl).

    get URL [--bearer-env VAR]   HTTPS/HTTP GET, optional bearer token from an env var
    messages BASE_URL MODEL      one minimal Anthropic Messages call, key from ANTHROPIC_API_KEY
    vsock PORT                   GET /api/tags over AF_VSOCK to the host (CID 2)
    tcp HOST PORT                GET /api/tags over plain TCP

Prints one JSON line. Exit 0 when an HTTP response came back (any status), 1 when
the connection itself failed.
"""

import json
import os
import socket
import sys
import urllib.error
import urllib.request


def emit(result: dict[str, object], ok: bool) -> None:
    print(json.dumps(result))
    sys.exit(0 if ok else 1)


def http(req: urllib.request.Request) -> None:
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            emit({"status": resp.status, "body": resp.read(4000).decode(errors="replace")}, True)
    except urllib.error.HTTPError as exc:
        emit({"status": exc.code, "body": exc.read(4000).decode(errors="replace")}, True)
    except (urllib.error.URLError, OSError) as exc:
        emit({"error": repr(exc)}, False)


def raw_get(sock: socket.socket) -> None:
    try:
        sock.settimeout(20)
        sock.sendall(b"GET /api/tags HTTP/1.0\r\nHost: localhost\r\n\r\n")
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        emit({"status_line": head.split(b"\r\n")[0].decode(), "body": body[:4000].decode()}, True)
    except OSError as exc:
        emit({"error": repr(exc)}, False)


def main() -> None:
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "get":
        req = urllib.request.Request(args[0])
        if len(args) == 3 and args[1] == "--bearer-env":
            req.add_header("Authorization", f"Bearer {os.environ[args[2]]}")
        http(req)
    elif cmd == "messages":
        body = {
            "model": args[1],
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        }
        req = urllib.request.Request(
            args[0].rstrip("/") + "/v1/messages",
            data=json.dumps(body).encode(),
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        http(req)
    elif cmd == "vsock":
        try:
            sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)  # type: ignore[attr-defined]  # Linux guest only
            sock.connect((2, int(args[0])))
        except OSError as exc:
            emit({"error": repr(exc)}, False)
        raw_get(sock)
    elif cmd == "tcp":
        try:
            sock = socket.create_connection((args[0], int(args[1])), timeout=20)
        except OSError as exc:
            emit({"error": repr(exc)}, False)
        raw_get(sock)
    else:
        sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
