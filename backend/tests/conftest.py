"""pytest 全局夹具。

三条硬约束：

1. **必须先改环境变量再导入 backend**：``backend.config.settings`` 是「导入即读环境变量」的单例，
   在 conftest 顶部改完环境变量，后续导入才会生效；
2. **库隔离**：测试用独立临时文件库，绝不碰 ``data/app.db``；
3. **强制 Mock**：``MOCK_MODE=1`` + 零延迟，用例不出网、不等待，才能满足 TDD 的秒级反馈。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="ai-panel-studio-tests-"))
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ["MOCK_MODE"] = "1"
os.environ["MOCK_LATENCY_MS"] = "0"
os.environ["SPEAK_INTERVAL_SECONDS"] = "0"
#: 真人节奏的「思考停顿」同样压到 0，用例不因表演节奏变慢
os.environ["THINK_INTERVAL_SECONDS"] = "0"
#: 轮次上限取配置允许的最小值，让 SSE 用例在亚秒级跑完
os.environ["MAX_ROUNDS"] = "6"

import pytest  # noqa: E402

from backend import models  # noqa: E402,F401  确保模型注册到 Base.metadata
from backend.database import Base, SessionLocal, engine  # noqa: E402
from backend.llm import LLMClient  # noqa: E402
from backend.tests.helpers import Seeded, seed_lineup, seed_session  # noqa: E402


@pytest.fixture()
def db():
    """每个用例一套干净的表结构（独立临时库，用例之间互不影响）。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def llm() -> LLMClient:
    """测试替身：零延迟 Mock 客户端，输出可重复。"""
    return LLMClient(mock=True, latency_ms=0)


@pytest.fixture()
def seeded(db) -> Seeded:
    """一场阵容已就绪的讨论：1 位主持人 + 3 位专家，状态 ``live``。"""
    session = seed_session(
        db, topic="四天工作制是否真的提高了效率", expert_count=3, status="live"
    )
    return seed_lineup(db, session)


@pytest.fixture(autouse=True)
def _reset_stream_state():
    """清空 SSE 广播中枢与后台任务登记表，保证用例之间不串流。"""
    from backend import discussion, stream

    stream.broker.reset()
    discussion.reset_discussions()
    yield
    stream.broker.reset()
    discussion.reset_discussions()

