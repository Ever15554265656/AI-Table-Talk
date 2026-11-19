"""兼容层：讨论引擎已迁移至 ``backend.discussion``。

- 新入口：``backend.discussion.run_discussion`` / ``start_discussion``；
- 旧入口：``stream_session_events``（回看 / 测试用）保持可用，内部直接委派。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from backend.discussion import stream_events
from backend.llm import LLMClient
from backend.schemas import SseEvent


async def stream_session_events(
    session_id: str, *, client: LLMClient | None = None
) -> AsyncIterator[SseEvent]:
    """跑完一场讨论并按序产出事件（不经过 SSE 广播中枢）。"""
    async for event in stream_events(session_id, client=client):
        yield event


__all__ = ["stream_session_events"]
