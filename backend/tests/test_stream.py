"""TDD 用例：SSE 事件流（PRD AC-13.x / docs/API.md §5）。

断言契约级行为，而不是实现细节：
- 一次连接内推出 ``message`` / ``status`` / ``consensus`` / ``phase`` / ``typing`` / ``done`` 六种事件；
- ``seq`` 从 1 连续递增（同时作为 SSE ``id``，供 ``Last-Event-ID`` 重放）；
- 结束事件为 ``ended``；
- 「看到的」与「存下的」一致：可见发言全部落库，内部事件（举手）一条都没有。
"""

from __future__ import annotations

import httpx

from backend.main import app
from backend.models import Message, SessionStatus
from backend.tests.helpers import parse_sse_frames

EXPECTED_EVENT_TYPES = {"message", "status", "consensus", "phase", "typing", "done"}


async def test_stream_emits_all_event_types(db, seeded):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            f"/api/sessions/{seeded.session.id}/events", timeout=30.0
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"

    events = parse_sse_frames(response.text)
    assert events, "SSE 流为空"

    assert {event.event for event in events} == EXPECTED_EVENT_TYPES
    assert events[-1].event == "done"
    assert events[-1].data.status == SessionStatus.ENDED.value
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert all(event.session_id == seeded.session.id for event in events)

    messages = [event.data.message for event in events if event.event == "message"]
    assert messages, "至少要有开场与专家发言"
    assert all(item.content.strip() for item in messages)
    assert all(not item.content.lstrip().startswith("{") for item in messages)

    # 释放测试会话的快照，读取引擎写入的最新数据
    db.rollback()
    stored = (
        db.query(Message)
        .filter(Message.session_id == seeded.session.id)
        .order_by(Message.round)
        .all()
    )
    assert len(stored) == len(messages), "事件的每一条发言都必须落库"
    assert [row.content for row in stored] == [item.content for item in messages]
    assert all("举手" not in row.content for row in stored)
