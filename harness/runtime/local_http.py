"""Local HTTP server for serving SWF + Ruffle assets."""

from __future__ import annotations

import contextlib
import http.server
import socket
import socketserver
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class LocalServer:
    base_url: str
    port: int
    thread: threading.Thread
    httpd: socketserver.TCPServer


def _find_free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve_directory(root: Path, port: Optional[int] = None) -> LocalServer:
    root = root.resolve()
    port = port or _find_free_port()

    handler = http.server.SimpleHTTPRequestHandler

    class RootedHandler(handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format: str, *args):
            return

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), RootedHandler)

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    return LocalServer(base_url=f"http://127.0.0.1:{port}", port=port, thread=t, httpd=httpd)
