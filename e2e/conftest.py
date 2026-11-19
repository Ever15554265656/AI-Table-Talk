"""E2E 环境夹具：自动拉起 Mock 后端（uvicorn）+ 前端（vite dev），用系统 Edge 驱动。

要点
----
1. **自动起停服务**：``api_server`` / ``web_server`` 是 session 级夹具，测试前拉起、
   结束后按进程树回收（Windows 用 ``taskkill /T /F``，避免 vite 残留 node 进程）。
2. **零成本**：后端强制 ``MOCK_MODE=true``，并把 ``SPEAK_INTERVAL_SECONDS`` /
   ``MOCK_LATENCY_MS`` 压到 0、``MAX_ROUNDS`` 压到下限 6，整场讨论几秒内跑完；
   不产生任何真实模型调用。
3. **库隔离**：数据库指向 ``e2e/.artifacts/e2e.db``，绝不读写仓库里的 ``data/app.db``。
4. **浏览器**：默认用系统自带 Edge（Playwright ``channel="msedge"``），
   因此**不需要**执行 ``playwright install`` 下载 Chromium；
   若本机没有 Edge，可用 ``E2E_BROWSER_CHANNEL=chrome`` 或预先装好 Chromium。
5. **可观测**：两端日志落在 ``e2e/.artifacts/{uvicorn,vite}.log``，
   服务没起来时把日志尾部直接拼进断言错误里。

用法（在仓库根目录）::

    .\\venv\\Scripts\\python.exe -m pytest e2e -q
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ARTIFACTS_DIR = PROJECT_ROOT / "e2e" / ".artifacts"
PYTHON = sys.executable

#: Mock 模式的加速参数（真实模型调用为 0 次）
BACKEND_ENV: dict[str, str] = {
    "APP_ENV": "e2e",
    "MOCK_MODE": "true",
    "MOCK_LATENCY_MS": "0",
    "SPEAK_INTERVAL_SECONDS": "0",
    "THINK_INTERVAL_SECONDS": "0",
    "MAX_ROUNDS": "6",
    "INSIGHT_EVERY_N_MESSAGES": "1",
}


# ---------------------------------------------------------------- 进程工具


def _free_port() -> int:
    """向系统要一个空闲端口（立刻释放，随后交给子进程绑定）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_ready(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, OSError):
        return False


def _tail(path: Path, limit: int = 3000) -> str:
    if not path.exists():
        return "（没有日志文件）"
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:] or "（日志为空）"


def _wait_ready(
    url: str, *, name: str, proc: subprocess.Popen, log_path: Path, timeout: float = 90.0
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"{name} 启动失败（退出码 {proc.returncode}）\n----- 日志 -----\n{_tail(log_path)}"
            )
        if _http_ready(url):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"{name} 在 {timeout:.0f}s 内没有就绪：{url}\n----- 日志 -----\n{_tail(log_path)}"
    )


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:  # pragma: no cover - 仅 Linux/macOS 开发机
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


# ---------------------------------------------------------------- 服务夹具


@pytest.fixture(scope="session")
def artifacts_dir() -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS_DIR


@pytest.fixture(scope="session")
def api_server(artifacts_dir: Path) -> str:
    """Mock 后端（uvicorn）：独立端口 + 独立 SQLite，产出 ``http://127.0.0.1:<port>``。"""
    port = _free_port()
    db_path = artifacts_dir / "e2e.db"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    env = {**os.environ, **BACKEND_ENV, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"}
    # 应用本身不在启动时建表（建表是 scripts/init_db.py 的职责），这里用同一份配置先建好
    subprocess.run(
        [PYTHON, "-c", "from backend.database import create_all; create_all()"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )

    log_path = artifacts_dir / "uvicorn.log"
    log = log_path.open("wb")
    proc = subprocess.Popen(
        [
            PYTHON,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(f"{base_url}/api/health", name="后端 uvicorn", proc=proc, log_path=log_path)
        yield base_url
    finally:
        _kill_tree(proc)
        log.close()


@pytest.fixture(scope="session")
def web_server(api_server: str, artifacts_dir: Path) -> str:
    """Vite dev（``/api`` 代理到 ``api_server``），产出 ``http://127.0.0.1:<port>/``。"""
    port = _free_port()
    env = {
        **os.environ,
        "VITE_API_TARGET": api_server,
        "BROWSER": "none",
        "CI": "1",  # 关掉 vite 的交互式快捷键与清屏
    }
    log_path = artifacts_dir / "vite.log"
    log = log_path.open("wb")
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    proc = subprocess.Popen(
        f'"{npm}" run dev -- --host 127.0.0.1 --port {port} --strictPort',
        cwd=FRONTEND_DIR,
        env=env,
        shell=True,  # npm 在 Windows 上是 .cmd，需要 shell 解析
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    app_url = f"http://127.0.0.1:{port}/"
    try:
        _wait_ready(app_url, name="前端 vite dev", proc=proc, log_path=log_path, timeout=120)
        yield app_url
    finally:
        _kill_tree(proc)
        log.close()


@pytest.fixture(scope="session")
def app_url(web_server: str) -> str:
    """页面入口（含尾斜杠），语义别名，便于用例里 `page.goto(app_url)`。"""
    return web_server


@pytest.fixture(scope="session")
def api(api_server: str) -> httpx.Client:
    """直连后端的 HTTP 客户端：用例用它取「事实基线」来对照页面渲染。"""
    with httpx.Client(base_url=api_server, timeout=15.0) as client:
        yield client


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    """默认用系统 Edge，避免 ``playwright install`` 下载浏览器（``E2E_BROWSER_CHANNEL=''`` 可关）。"""
    channel = os.environ.get("E2E_BROWSER_CHANNEL", "msedge")
    return {**browser_type_launch_args, "channel": channel} if channel else browser_type_launch_args
