"""TDD 用例：共识 / 分歧提炼（PRD AC-5.3 / AC-5.4）。

关键断言：
- 条目写入 ``consensus_items``，``type`` 由中文「共识 / 分歧」映射为 ``agree`` / ``disagree``；
- ``speaker_names`` 必须按姓名映射回本场 ``expert_id``，映射不到的姓名（幻觉）直接丢弃。
"""

from __future__ import annotations

from backend.models import ConsensusItem, ConsensusType
from backend.services.consensus import extract_insights
from backend.tests.helpers import seed_messages


def test_insights_written_to_consensus_items(db, llm, seeded):
    """固定 JSON → 两条条目落库，类型正确、专家归属可追溯。"""
    seed_messages(
        db,
        seeded.session,
        [seeded.host, seeded.guests[0], seeded.guests[1], seeded.guests[2]],
        [
            "先请两位说说，四天工作制到底改善了哪一段效率。",
            "在知识型岗位它确实减少了无效会议，但必须同步改考核方式。",
            "我不同意，交付节奏一压缩，压力最后还是落到一线。",
            "法律上还有个洞：工时不减、加班认定不清，它就会变成隐性加班。",
        ],
    )

    created = extract_insights(db, seeded.session, client=llm)

    rows = db.query(ConsensusItem).filter(ConsensusItem.session_id == seeded.session.id).all()
    assert len(created) == 2
    assert len(rows) == 2, "条目必须真正落库"
    assert {row.type for row in rows} == {
        ConsensusType.AGREE.value,
        ConsensusType.DISAGREE.value,
    }

    valid_ids = {expert.id for expert in seeded.everyone}
    agree = next(row for row in rows if row.type == ConsensusType.AGREE.value)
    disagree = next(row for row in rows if row.type == ConsensusType.DISAGREE.value)

    assert agree.content.strip()
    assert len(agree.content) <= 60
    assert len(agree.expert_ids) >= 2, "共识至少要有两位真实发言人"
    assert set(agree.expert_ids) <= valid_ids

    assert disagree.content.strip()
    assert disagree.expert_ids, "幻觉姓名被剔除后仍应保留真实发言人"
    assert set(disagree.expert_ids) <= valid_ids
    assert all(expert_id in valid_ids for row in rows for expert_id in row.expert_ids)
