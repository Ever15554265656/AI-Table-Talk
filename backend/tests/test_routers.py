"""TDD 用例：四组 REST 路由。

- ``POST /api/sessions``：建会话 + 生成阵容，返回 ``201 SessionDetail``（主持人在前）；
- ``GET /api/sessions``：首页列表，支持状态过滤与分页，按创建时间倒序；
- ``GET /api/sessions/{id}/experts``：席位表（主持人优先）＋未知会话 404；
- ``GET /api/sessions/{id}/messages``：回看分页（``after_round``）＋未知会话 404；
- ``GET /api/sessions/{id}/insights``：共识 / 分歧整组快照（回放用）＋未知会话 404；
- ``GET /api/stream/{id}``：SSE 端点，跑完整场讨论；旧路径 ``/events`` 保持可用。
"""

from __future__ import annotations

import httpx

from backend.main import app
from backend.models import ConsensusItem, DiscussionSession, Expert, Message, SessionStatus
from backend.tests.helpers import (
    parse_sse_frames,
    seed_lineup,
    seed_messages,
    seed_session,
)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def test_create_session_returns_detail_with_lineup(db):
    async with _client() as client:
        response = await client.post(
            "/api/sessions",
            json={"topic": "  四天工作制是否真的提高了效率  ", "expert_count": 3},
        )

    assert response.status_code == 201
    body = response.json()

    assert body["topic"] == "四天工作制是否真的提高了效率", "话题需裁剪首尾空白"
    assert body["status"] == SessionStatus.CREATED.value
    assert body["expert_count"] == 3
    assert body["expert_total"] == 4
    assert body["message_count"] == 0
    assert len(body["experts"]) == 4
    assert body["experts"][0]["is_host"] is True
    assert all(expert["is_host"] is False for expert in body["experts"][1:])
    assert len({expert["color"] for expert in body["experts"]}) == 4

    db.rollback()
    rows = db.query(Expert).filter(Expert.session_id == body["id"]).all()
    assert len(rows) == 4, "阵容必须真正落库"


async def test_create_session_rejects_invalid_expert_count(db):
    async with _client() as client:
        response = await client.post(
            "/api/sessions", json={"topic": "只讨论一件事", "expert_count": 99}
        )

    assert response.status_code == 422


async def test_list_sessions_is_newest_first_and_filterable(db):
    first = seed_session(db, topic="第一个议题", expert_count=2, status="live")
    seed_lineup(db, first)
    second = seed_session(db, topic="第二个议题", expert_count=3, status="ended")
    seeded = seed_lineup(db, second)
    seed_messages(db, second, seeded.guests[:2], ["一句发言。", "又一句发言。"])

    async with _client() as client:
        listed = await client.get("/api/sessions", params={"limit": 10})
        ended_only = await client.get("/api/sessions", params={"status": "ended"})
        capped = await client.get("/api/sessions", params={"limit": 1})

    assert listed.status_code == 200
    topics = [item["topic"] for item in listed.json()]
    assert topics.index("第二个议题") <= topics.index("第一个议题"), "按创建时间倒序"

    by_id = {item["id"]: item for item in listed.json()}
    assert by_id[second.id]["expert_total"] == 4
    assert by_id[second.id]["message_count"] == 2
    assert by_id[first.id]["message_count"] == 0

    assert [item["id"] for item in ended_only.json()] == [second.id]
    assert len(capped.json()) == 1


async def test_experts_endpoint_returns_host_first(db):
    session = seed_session(db, topic="席位顺序", expert_count=3)
    seeded = seed_lineup(db, session)

    async with _client() as client:
        response = await client.get(f"/api/sessions/{session.id}/experts")
        missing = await client.get("/api/sessions/not-exists/experts")

    assert response.status_code == 200
    seats = response.json()
    assert [seat["id"] for seat in seats][0] == seeded.host.id
    assert all(seat["session_id"] == session.id for seat in seats)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "SESSION_NOT_FOUND"


async def test_messages_endpoint_pages_by_round(db):
    session = seed_session(db, topic="回看分页", expert_count=2)
    seeded = seed_lineup(db, session)
    seed_messages(
        db,
        session,
        [seeded.host, seeded.guests[0], seeded.guests[1], seeded.guests[0]],
        ["第一条。", "第二条。", "第三条。", "第四条。"],
    )

    async with _client() as client:
        everything = await client.get(f"/api/sessions/{session.id}/messages")
        tail = await client.get(
            f"/api/sessions/{session.id}/messages", params={"after_round": 2}
        )
        missing = await client.get("/api/sessions/not-exists/messages")

    assert [item["round"] for item in everything.json()] == [1, 2, 3, 4]
    assert [item["round"] for item in tail.json()] == [3, 4]
    assert tail.json()[0]["content"] == "第三条。"
    assert tail.json()[0]["expert_name"] == seeded.guests[1].name
    assert tail.json()[0]["expert_color"].startswith("#")
    assert missing.status_code == 404


async def test_insights_endpoint_returns_grouped_snapshot(db):
    """回放用快照：按 ``type`` 分成 ``agree`` / ``disagree``，顺序稳定。"""
    session = seed_session(db, topic="共识快照", expert_count=2, status="ended")
    seeded = seed_lineup(db, session)
    db.add_all(
        [
            ConsensusItem(
                id=f"{session.id}-c01",
                session_id=session.id,
                type="agree",
                content="双方都承认成本是核心变量。",
                expert_ids=[seeded.host.id, seeded.guests[0].id],
            ),
            ConsensusItem(
                id=f"{session.id}-c02",
                session_id=session.id,
                type="disagree",
                content="是否继续投入，双方判断相反。",
                expert_ids=[seeded.guests[1].id],
            ),
            ConsensusItem(
                id=f"{session.id}-c03",
                session_id=session.id,
                type="agree",
                content="都需要明确的退出机制。",
                expert_ids=[seeded.host.id],
            ),
        ]
    )
    db.commit()

    async with _client() as client:
        response = await client.get(f"/api/sessions/{session.id}/insights")
        missing = await client.get("/api/sessions/not-exists/insights")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session.id
    assert [item["type"] for item in body["agree"]] == ["agree", "agree"]
    assert [item["type"] for item in body["disagree"]] == ["disagree"]
    assert body["disagree"][0]["expert_ids"] == [seeded.guests[1].id]
    assert body["agree"][0]["content"] == "双方都承认成本是核心变量。"

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "SESSION_NOT_FOUND"


async def test_stream_endpoint_runs_discussion_once(db, seeded):
    async with _client() as client:
        response = await client.get(
            f"/api/stream/{seeded.session.id}", timeout=30.0
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"

    events = parse_sse_frames(response.text)
    assert {event.event for event in events} == {
        "message",
        "status",
        "consensus",
        "phase",
        "typing",
        "done",
    }
    assert events[-1].event == "done"
    assert [event.seq for event in events] == list(range(1, len(events) + 1))

    db.rollback()
    db.expire_all()
    session = db.get(DiscussionSession, seeded.session.id)
    assert session is not None
    assert session.status == SessionStatus.ENDED.value
    count = db.query(Message).filter(Message.session_id == seeded.session.id).count()
    assert count == events[-1].data.message_count


async def test_legacy_events_path_still_serves_the_same_stream(db, seeded):
    async with _client() as client:
        response = await client.get(
            f"/api/sessions/{seeded.session.id}/events", timeout=30.0
        )

    assert response.status_code == 200
    events = parse_sse_frames(response.text)
    assert events[-1].event == "done"


async def test_stream_endpoint_404_for_unknown_session(db):
    async with _client() as client:
        response = await client.get("/api/stream/not-exists")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SESSION_NOT_FOUND"
