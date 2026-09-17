"""起停 opencode serve（规格 §7.1）。"""

from __future__ import annotations

import base64
import os
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


def build_serve_args(port: int) -> list[str]:
    return ["serve", "--hostname", "127.0.0.1", "--port", str(port)]


def basic_auth_header(password: str) -> str:
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    return f"Basic {token}"


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# 本机回环 HTTP 必须忽视宿主的代理环境变量。实测（本机 http_proxy=http://127.0.0.1:7897
# 且 ALL_PROXY=socks5://127.0.0.1:7897，NO_PROXY 里明明列了 127.0.0.1）：httpx 的 mount 表把
# http:// 与 all:// 排在 no_proxy 派生出的 all://127.0.0.1 之前，代理 mount 先命中，于是连
# 自己起的 serve 都连不上 —— 表现为 ImportError: Using SOCKS proxy, but the 'socksio'
# package is not installed（装了 socksio 也只是把回环请求发给代理）。NO_PROXY 救不了它。
# 注意只管客户端：serve 子进程仍原样继承 env，它的出网请求该走代理还走代理。
LOOPBACK_OPTIONS: dict[str, Any] = {"trust_env": False}


def wait_healthy(base_url: str, password: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = "unknown"
    with httpx.Client(timeout=2.0, **LOOPBACK_OPTIONS) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(
                    f"{base_url}/global/health",
                    headers={"authorization": basic_auth_header(password)},
                )
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("healthy") is True:
                        return
                    last_error = f"health 返回 {payload}"
                else:
                    last_error = f"HTTP {response.status_code}"
            except Exception as error:  # noqa: BLE001 - 就绪轮询要吞掉所有连接类异常
                last_error = str(error)
            time.sleep(0.25)
    raise RuntimeError(f"opencode serve 在 {timeout_s}s 内未就绪：{last_error}")


@dataclass
class ServeHandle:
    base_url: str
    password: str
    port: int
    pid: int | None
    log_path: str
    process: subprocess.Popen
    log_file: object

    def stop(self) -> None:
        from ..shell_toolchain.execute import kill_tree

        try:
            self.log_file.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        if self.pid is not None:
            kill_tree(self.pid)


def start_serve(opencode_path: str, run_dir: str, timeout_s: float = 20.0) -> ServeHandle:
    """起一个独占的 opencode serve，stdout/stderr 收进运行目录的 server.log。"""
    port = pick_free_port()
    password = secrets.token_hex(24)
    log_path = Path(run_dir, "server.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8", errors="replace")

    popen_kwargs: dict[str, Any] = {
        "cwd": run_dir,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "env": {**os.environ, "OPENCODE_SERVER_PASSWORD": password},
    }
    # serve 必须活在自己的进程组里，理由与 shell_toolchain.execute 完全相同（那里是实测结论）：
    # kill_tree 在 POSIX 走 os.killpg(os.getpgid(pid), SIGKILL)，而 Popen 子进程默认继承调用方的
    # 进程组 —— 少了这一行，os.getpgid(serve_pid) 返回的就是**我们自己的**进程组，
    # 于是 dispose()/失败路径的 killpg 会把 CLI/应用连同它一起 SIGKILL（应用自杀）。
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(
        [opencode_path, *build_serve_args(port)],
        **popen_kwargs,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_healthy(base_url, password, timeout_s)
    except RuntimeError:
        from ..shell_toolchain.execute import kill_tree

        if process.pid is not None:
            kill_tree(process.pid)
        log_file.close()
        raise

    return ServeHandle(
        base_url=base_url,
        password=password,
        port=port,
        pid=process.pid,
        log_path=str(log_path),
        process=process,
        log_file=log_file,
    )
