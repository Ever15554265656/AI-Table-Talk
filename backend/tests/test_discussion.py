"""TDD 用例：``backend/discussion.py`` 讨论主循环。

验收口径（需求 4）：
1. 阶段推进固定为 ``opening → debate → wrap_up``；
2. 开场与收尾都由主持人说，中间每轮只由一位专家说；
3. 同一人不会连着说两轮（调度里用 ``last_speaker_id`` 排除）；
4. 每 ``INSIGHT_EVERY_N_MESSAGES`` 条发言刷新一次共识（``revision`` 递增）；
5. 席位状态按 ``preparing → speaking → idle`` 变化，``focus`` 只在说话时公开；
6. 结束事件为 ``done``，带总结与真实发言条数，会话状态落库为 ``ended``。
"""

from __future__ import annotations

from backend.config import settings
from backend.discussion import run_discussion, stream_events
from backend.models import Message, SessionStatus
from backend.schemas import PanelistState

MAX_ROUNDS = settings.max_rounds
EVERY = max(1, settings.insight_every_n_messages)


async def _collect(db, seeded) -> list:
    return [event async for event in stream_events(seeded.session.id)]


async def test_phases_advance_opening_debate_wrap_up(db, seeded):
    """阶段顺序固定，且开场 / 收尾都是主持人。"""
    events = await _collect(db, seeded)

    phases = [event.data.phase.value for event in events if event.event == "phase"]
    assert phases == ["opening", "debate", "wrap_up"]

    messages = [event.data.message for event in events if event.event == "message"]
    assert len(messages) == 1 + MAX_ROUNDS + 1
    assert messages[0].expert_id == seeded.host.id, "开场必须由主持人说"
    assert messages[-1].expert_id == seeded.host.id, "收尾必须由主持人说"
    assert messages[0].round == 1
    assert [item.round for item in messages] == list(range(1, len(messages) + 1))


async def test_debate_does_not_repeat_the_same_speaker(db, seeded):
    """调度不得让同一位专家连说两轮。"""
    events = await _collect(db, seeded)
    experts = [event.data.message.expert_id for event in events if event.event == "message"]
    debate = experts[1:-1]

    assert all(left != right for left, right in zip(debate, debate[1:])), debate


async def test_consensus_is_refreshed_every_n_messages(db, seeded):
    """每 N 条发言刷新一次共识，两类条目各推一次且 ``revision`` 递增。"""
    events = await _collect(db, seeded)
    consensus = [event for event in events if event.event == "consensus"]

    assert consensus, "必须提炼出共识事件"
    assert {event.data.type.value for event in consensus} == {"agree", "disagree"}
    assert sorted({event.data.revision for event in consensus}) == [
        revision for revision in range(1, MAX_ROUNDS // EVERY + 1)
    ]
    assert all(
        item.content.strip() for event in consensus for item in event.data.items
    )


async def test_panelist_states_cycle_preparing_speaking_idle(db, seeded):
    """席位三态：先 preparing（带 focus），再 speaking，说完回 idle。"""
    events = await _collect(db, seeded)
    statuses = [event.data for event in events if event.event == "status"]

    states = {
        panelist.state for data in statuses for panelist in data.panelists
    }
    assert {
        PanelistState.PREPARING,
        PanelistState.SPEAKING,
        PanelistState.IDLE,
    } <= states

    for data in statuses:
        speaking = [
            panelist
            for panelist in data.panelists
            if panelist.state is not PanelistState.IDLE
        ]
        assert len(speaking) <= 1, "同一时刻最多一人占据话筒"
        assert all(
            panelist.state is PanelistState.IDLE and panelist.focus is None
            for panelist in data.panelists
            if panelist not in speaking
        )


async def test_done_event_carries_summary_and_real_counts(db, seeded):
    """``done`` 收尾：总结就绪、条数与库内一致、会话落库为 ended。"""
    events = await _collect(db, seeded)

    assert [event.event for event in events][-1] == "done"
    done = events[-1].data
    assert done.status == SessionStatus.ENDED.value
    assert done.total_rounds == MAX_ROUNDS
    assert done.summary is not None
    assert done.summary.sections
    assert done.degraded is False

    db.rollback()
    db.expire_all()
    rows = db.query(Message).filter(Message.session_id == seeded.session.id).all()
    assert done.message_count == len(rows) == 1 + MAX_ROUNDS + 1

    session = db.get(type(seeded.session), seeded.session.id)
    assert session is not None
    assert session.status == SessionStatus.ENDED.value
    assert all("举手" not in row.content for row in rows)


async def test_publish_callback_sees_the_same_stream(db, seeded):
    """``run_discussion`` 的 ``publish`` 回调与服务端推送的是同一批事件。"""
    published: list = []
    await run_discussion(seeded.session.id, publish=published.append)

    events = await _collect(db, seeded)

    assert [event.seq for event in published] == list(range(1, len(published) + 1))
    assert [event.event for event in published] == [event.event for event in events]
    assert published[-1].event == "done"
