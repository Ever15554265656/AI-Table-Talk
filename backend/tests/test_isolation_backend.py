"""两场讨论并行推进时互不串扰（PRD R9 / ER §4 的机器可验证断言）。

断言四层隔离：
1. 事件信封：每个事件的 ``session_id`` 必须等于它所属的场次；
2. 发言：A 的正文只谈 A 的议题关键词，B 同理；两场的 ``round`` 各自从 1 连续；
3. 共识：两场各取本域死结，不出现对方的条目；
4. 席位：两场 expert_id 完全不重叠（同名也不会串）。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func

from backend import arguments
from backend.discussion import run_discussion
from backend.models import ConsensusItem, DiscussionSession, Message, SessionStatus
from backend.tests.helpers import seed_lineup, seed_session

TOPIC_A = "城市该不该继续限行"
TOPIC_B = "四天工作制是否值得推广？"


async def test_two_sessions_run_concurrently_without_cross_talk(db):
    """asyncio.gather 同时推进 A/B 两场，逐层断言互不串扰。"""
    session_a = seed_session(db, topic=TOPIC_A, expert_count=3, status="live")
    session_b = seed_session(db, topic=TOPIC_B, expert_count=3, status="live")
    seed_lineup(db, session_a)
    seed_lineup(db, session_b)

    published: dict[str, list] = {session_a.id: [], session_b.id: []}

    await asyncio.gather(
        run_discussion(session_a.id, publish=published[session_a.id].append),
        run_discussion(session_b.id, publish=published[session_b.id].append),
    )

    # 1) 事件信封不串场
    for session_id, events in published.items():
        assert events, f"{session_id} 没有产出事件"
        assert all(event.session_id == session_id for event in events)
        assert events[-1].event == "done"

    # 2) 发言正文互不越界
    db.rollback()
    db.expire_all()
    keyword_a = arguments.primary_keyword(TOPIC_A)
    keyword_b = arguments.primary_keyword(TOPIC_B)
    assert keyword_a != keyword_b

    for session, own, other in (
        (session_a, keyword_a, keyword_b),
        (session_b, keyword_b, keyword_a),
    ):
        rows = (
            db.query(Message)
            .filter(Message.session_id == session.id)
            .order_by(Message.round)
            .all()
        )
        assert rows, "每场都必须真的产出发言"
        assert [row.round for row in rows] == list(range(1, len(rows) + 1)), "轮次各自连续"
        text = "".join(row.content for row in rows)
        assert own in text, f"本场发言必须咬自己的议题：{own}"
        assert other not in text, f"不能出现另一场的议题关键词：{other}"

    # 3) 共识条目按场归属，且只写本域死结
    for session, own_topic, other_topic in (
        (session_a, TOPIC_A, TOPIC_B),
        (session_b, TOPIC_B, TOPIC_A),
    ):
        items = (
            db.query(ConsensusItem)
            .filter(ConsensusItem.session_id == session.id)
            .all()
        )
        assert items, "每场都必须沉淀共识或分歧"
        assert all(item.session_id == session.id for item in items)
        contents = "".join(item.content for item in items)
        assert arguments.deadlock_for(own_topic) in contents
        assert arguments.deadlock_for(other_topic) not in contents

    # 4) 席位 id 不重叠
    ids_a = {row.expert_id for row in db.query(Message).filter(Message.session_id == session_a.id)}
    ids_b = {row.expert_id for row in db.query(Message).filter(Message.session_id == session_b.id)}
    assert ids_a and ids_b and ids_a.isdisjoint(ids_b), "两场席位 id 必须完全隔离"

    # 两场都正常收尾
    assert (
        db.query(func.count(DiscussionSession.id))
        .filter(
            DiscussionSession.id.in_([session_a.id, session_b.id]),
            DiscussionSession.status == SessionStatus.ENDED.value,
        )
        .scalar()
        == 2
    )
