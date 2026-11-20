"""真实模式的重试 / 降级 / 超时用例（全部零网络、零真实计费）。

覆盖：
1. 远端连续失败 → 重试 3 次后改用 Mock 内容，并标记 ``degraded``；
2. 第 2 次尝试成功 → 使用真实内容，且只调用 2 次、不标记降级；
3. 阵容生成失败 → 降级 Mock 阵容（不抛错、不 502）；
4. 讨论层把降级状态写进 ``done.degraded``；
5. OpenAI / AsyncOpenAI 构造时带上 ``LLM_TIMEOUT_SECONDS``。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend import arguments
from backend.config import settings
from backend.discussion import stream_events
from backend.flows.lineup import generate_lineup
from backend.llm import (
    LLM_MAX_ATTEMPTS,
    SCENE_LINEUP,
    SCENE_SPEAK,
    LLMClient,
    LLMError,
    mock_text,
)
from backend.tests.helpers import seed_session

#: 发言场景的最小 payload（Mock 生成器需要议题 / 席位 / 轮次规模）
SPEAK_PAYLOAD: dict[str, object] = {
    "topic": "城市该不该继续限行",
    "purpose": "debate",
    "speaker": "林岚",
    "profession": "城市规划师",
    "stance": "支持「城市该不该继续限行」：短期能压住峰值",
    "turn": 1,
    "total_rounds": 6,
    "expert_count": 3,
}


class _RemoteStub(LLMClient):
    """强制走真实分支，用桩替换远端调用：可指定「前 N 次失败」或「永远失败」。"""

    def __init__(self, *, fail_times: int | None = None, answer: str = "真实模式生成的内容。") -> None:
        super().__init__(mock=False, latency_ms=0)
        #: ``None`` 表示永远失败；整数表示前 N 次失败
        self.fail_times = fail_times
        self.answer = answer
        self.attempts: list[str] = []

    def _should_fail(self, kind: str) -> bool:
        self.attempts.append(kind)
        if self.fail_times is None:
            return True
        return self.attempts.count(kind) <= self.fail_times

    def _remote_stream(self, prompt, payload):  # noqa: ANN001
        if self._should_fail("sync"):
            raise LLMError("模拟上游失败")
        yield self.answer

    async def _remote_astream(self, prompt, payload):  # noqa: ANN001
        if self._should_fail("async"):
            raise LLMError("模拟上游失败")
        yield self.answer


def test_sync_stream_retries_then_falls_back_to_mock(monkeypatch):
    """连续失败：尝试 3 次 → 改用 Mock 内容 + 标记降级。"""
    monkeypatch.setattr("backend.llm.LLM_RETRY_DELAY_SECONDS", 0)
    client = _RemoteStub(fail_times=None)

    out = "".join(client.stream(scene=SCENE_SPEAK, prompt="p", payload=SPEAK_PAYLOAD))

    assert out == mock_text(SCENE_SPEAK, SPEAK_PAYLOAD), "兜底必须是 Mock 生成的内容"
    assert client.attempts == ["sync"] * LLM_MAX_ATTEMPTS
    assert client.degraded is True
    assert SCENE_SPEAK in client.degraded_scenes


def test_sync_stream_succeeds_on_second_attempt(monkeypatch):
    """第一次失败、第二次成功：使用真实内容，且只调用 2 次、不标记降级。"""
    monkeypatch.setattr("backend.llm.LLM_RETRY_DELAY_SECONDS", 0)
    client = _RemoteStub(fail_times=1, answer="第二次成功了。")

    out = "".join(client.stream(scene=SCENE_SPEAK, prompt="p", payload=SPEAK_PAYLOAD))

    assert out == "第二次成功了。"
    assert len(client.attempts) == 2
    assert client.degraded is False
    assert client.degraded_scenes == []


async def test_async_stream_falls_back_to_mock(monkeypatch):
    """SSE 用的异步入口走同一套重试 / 兜底策略。"""
    monkeypatch.setattr("backend.llm.LLM_RETRY_DELAY_SECONDS", 0)
    client = _RemoteStub(fail_times=None)

    chunks = [
        chunk
        async for chunk in client.astream(
            scene=SCENE_SPEAK, prompt="p", payload=SPEAK_PAYLOAD
        )
    ]

    assert "".join(chunks) == mock_text(SCENE_SPEAK, SPEAK_PAYLOAD)
    assert client.attempts == ["async"] * LLM_MAX_ATTEMPTS
    assert client.degraded is True


class _RaisingLLM(LLMClient):
    """连兜底都不给机会的桩：直接抛 ``LLMError``（模拟客户端之外的失败）。"""

    def __init__(self) -> None:
        super().__init__(mock=False, latency_ms=0)
        self.calls = 0

    def complete(self, *, scene, prompt, payload):  # noqa: ANN001
        self.calls += 1
        raise LLMError("模拟总失败")


def test_lineup_falls_back_to_mock_instead_of_raising(db):
    """阵容生成失败 → 用 Mock 阵容继续（不抛错、不 502），并标记降级。"""
    session = seed_session(db, topic="城市该不该继续限行", expert_count=3)
    client = _RaisingLLM()

    experts = generate_lineup(db, session, topic=session.topic, client=client)

    assert len(experts) == 4, "主持人 + 3 位专家"
    assert client.degraded is True
    assert SCENE_LINEUP in client.degraded_scenes


async def test_discussion_marks_degraded_when_upstream_keeps_failing(
    db, seeded, monkeypatch
):
    """讨论层：真实模式全挂时本场仍然跑完，且 ``done.degraded`` 为真。"""
    monkeypatch.setattr("backend.llm.LLM_RETRY_DELAY_SECONDS", 0)
    client = _RemoteStub(fail_times=None)

    events = [event async for event in stream_events(seeded.session.id, client=client)]
    done = events[-1]

    assert done.event == "done"
    assert done.data.degraded is True, "降级标记必须随 done 下发"
    assert client.degraded is True

    messages = [event.data.message for event in events if event.event == "message"]
    debate = messages[1:-1]
    keyword = arguments.primary_keyword(seeded.session.topic)
    assert debate, "降级也要把整场跑完"
    assert all(keyword in item.content for item in debate), "兜底内容仍然是 Mock 论证"


def test_openai_clients_receive_configured_timeout(monkeypatch):
    """``LLM_TIMEOUT_SECONDS`` 必须真的传给 OpenAI / AsyncOpenAI 客户端。"""
    config = replace(
        settings,
        mock_mode=False,
        deepseek_api_key="test-key-not-real",
        llm_timeout_seconds=12.5,
    )
    captured: dict[str, dict[str, object]] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured["sync"] = kwargs

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            captured["async"] = kwargs

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)

    client = LLMClient(config=config)
    client._ensure_sync_client()
    client._ensure_async_client()

    assert captured["sync"]["timeout"] == pytest.approx(12.5)
    assert captured["async"]["timeout"] == pytest.approx(12.5)
