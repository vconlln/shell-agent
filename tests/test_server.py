import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tu_shell_agent.opencode_adapter.server import (
    basic_auth_header,
    build_serve_args,
    pick_free_port,
    wait_healthy,
)


def test_build_serve_args_binds_loopback_with_explicit_port():
    assert build_serve_args(4123) == ["serve", "--hostname", "127.0.0.1", "--port", "4123"]


def test_basic_auth_header_encodes_opencode_user():
    import base64

    expected = base64.b64encode(b"opencode:secret").decode()
    assert basic_auth_header("secret") == f"Basic {expected}"


def test_pick_free_port_returns_usable_port():
    port = pick_free_port()
    assert 1024 < port < 65536


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"healthy": True, "version": "1.18.31"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静音
        return


def test_wait_healthy_returns_once_server_answers():
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_healthy(f"http://127.0.0.1:{server.server_port}", "pw", timeout_s=5)
    finally:
        server.shutdown()


def test_wait_healthy_raises_on_dead_port():
    port = pick_free_port()
    with pytest.raises(RuntimeError, match="未就绪"):
        wait_healthy(f"http://127.0.0.1:{port}", "pw", timeout_s=1)
