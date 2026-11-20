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
    """阶段顺序固定：开场 → 每轮交锋 → 收尾，且开场 / 收尾都是主持人。"""
    events = await _collect(db, seeded)

    phases = [event.data.phase.value for event in events if event.event == "phase"]
    assert phases[0] == "opening"
    assert phases[-1] == "wrap_up"
    assert set(phases) <= {"opening", "debate", "wrap_up"}
    assert phases.count("debate") >= 2, "第一轮之前一次，之后每轮一次"

    messages = [event.data.message for event in events if event.event == "message"]
    guests = len(seeded.guests)
    rounds = max(1, -(-MAX_ROUNDS // guests))
    assert len(messages) == 1 + rounds * guests + 1
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


async def test_speakers_rotate_evenly_by_round(db, seeded):
    """均衡轮转：每轮全员各一次、相邻两轮顺序不同、全场次数差不超过 1。"""
    events = await _collect(db, seeded)
    debate = [
        event.data.message
        for event in events
        if event.event == "message"
    ][1:-1]
    guests = len(seeded.guests)

    assert debate and len(debate) % guests == 0, "每轮必须全员各说一次"
    counts: dict[str, int] = {}
    for item in debate:
        counts[item.expert_id] = counts.get(item.expert_id, 0) + 1
    assert max(counts.values()) - min(counts.values()) <= 1
    assert len(counts) == guests, "不能有人全程没说话"

    orders = [
        tuple(item.expert_id for item in debate[start : start + guests])
        for start in range(0, len(debate), guests)
    ]
    for previous, current in zip(orders, orders[1:]):
        assert current != previous, "相邻两轮顺序不能完全一样"
        assert current[0] != previous[-1], "同一人不能连说两条"


async def test_typing_event_precedes_every_speech(db, seeded):
    """typing 事件：每条发言前先宣布「谁正在发言」，且与随后的 message 是同一人。"""
    events = await _collect(db, seeded)
    typing = [event for event in events if event.event == "typing"]
    messages = [event.data.message for event in events if event.event == "message"]

    assert len(typing) == len(messages), "每条发言（含开场 / 收尾）都要有一次 typing"
    assert all(event.data.expert_name for event in typing)
    for index, event in enumerate(events):
        if event.event != "typing":
            continue
        following = next(
            (item for item in events[index + 1 :] if item.event == "message"), None
        )
        assert following is not None, "typing 之后必须跟上一条发言"
        assert following.data.message.expert_id == event.data.expert_id


async def test_consensus_is_refreshed_every_n_messages(db, seeded):
    """共识/分歧在讨论中途增量推送（不是等到结束才生成）。"""
    events = await _collect(db, seeded)
    consensus = [event for event in events if event.event == "consensus"]
    messages = [event for event in events if event.event == "message"]
    done_index = next(index for index, event in enumerate(events) if event.event == "done")

    assert consensus, "必须提炼出共识事件"
    assert {event.data.type.value for event in consensus} == {"agree", "disagree"}
    assert sorted({event.data.revision for event in consensus}) == [
        revision for revision in range(1, MAX_ROUNDS // EVERY + 1)
    ]
    assert all(
        item.content.strip() for event in consensus for item in event.data.items
    )
    # 实时性：第一次刷新必须早于「最后一条发言」，即讨论进行中就已经推过
    first_consensus = next(
        index for index, event in enumerate(events) if event.event == "consensus"
    )
    last_message = max(
        index for index, event in enumerate(events) if event.event == "message"
    )
    assert first_consensus < last_message < done_index, "共识必须实时推送，不能只在结束时生成"
    assert messages, "共识刷新时场上必须已有发言"


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
        # 公开关注点（思考摘要）必须随状态一起下发，前端状态小窗才有内容可显示
        for panelist in speaking:
            assert panelist.focus and panelist.focus.strip(), "发言/准备席位必须带 focus"


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
    """``run_discussion`` 的事件经 ``publish`` 原样输出；已经跑完的场次不会重跑。"""
    published: list = []
    await run_discussion(seeded.session.id, publish=published.append)

    assert [event.seq for event in published] == list(range(1, len(published) + 1))
    assert published[-1].event == "done"
    assert any(event.event == "message" for event in published)
    assert any(event.event == "typing" for event in published)

    db.rollback()
    db.expire_all()
    before = (
        db.query(Message).filter(Message.session_id == seeded.session.id).count()
    )

    # 再订阅一次：只补 phase / status / done，不重新生成已发出的发言
    events = await _collect(db, seeded)

    db.rollback()
    db.expire_all()
    after = (
        db.query(Message).filter(Message.session_id == seeded.session.id).count()
    )
    assert after == before, "已结束的场次不许重跑"
    assert not [event for event in events if event.event == "message"]
    assert events[-1].event == "done"
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
