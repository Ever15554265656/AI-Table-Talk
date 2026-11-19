"""讨论主循环：主持人开场 → 循环交锋 → 收尾总结（对应需求 4）。

编排顺序
--------

::

    status(live) → phase(opening)
      → 主持人开场（preparing → speaking → message → idle）
    phase(debate)
      → 每轮：调度 → status(preparing, focus) → status(speaking)
              → 流式发言 → 落库 → message → status(idle)
      → 每 INSIGHT_EVERY_N_MESSAGES 条发言：共识提炼 → consensus(agree) + consensus(disagree)
    phase(wrap_up) → 主持人总结（message）→ done → 会话置 ended

设计约定
--------

1. **只编排，不实现规则**：调度、发言、提炼、总结的结果都来自 ``flows/``；
2. **先落库再推送**：``message`` 事件永远由已提交的 ORM 行构造，保证「看到的 = 存下的」；
3. **可复用**：``run_discussion`` 只接受一个 ``publish`` 回调，SSE 端点接广播中枢，
   测试接 ``list.append``，两边行为完全一致；
4. **失败也要收尾**：任一环节抛错都发一个 ``degraded=True`` 的 ``done``，让前端有终点。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Callable

from sqlalchemy import func

from backend.config import settings
from backend.database import SessionLocal
from backend.flows import consensus as consensus_flow
from backend.flows import opening as opening_flow
from backend.flows import scheduling as scheduling_flow
from backend.flows import speaking as speaking_flow
from backend.flows import summary as summary_flow
from backend.flows import turn_taking as turn_flow
from backend.llm import LLMClient, get_llm
from backend.models import (
    ConsensusItem,
    ConsensusType,
    DiscussionSession,
    Expert,
    Message,
    SessionStatus,
)
from backend.schemas import (
    ConsensusEvent,
    ConsensusEventData,
    ConsensusItemOut,
    DiscussionPhase,
    DoneEvent,
    DoneEventData,
    MessageEvent,
    MessageEventData,
    MessageOut,
    PanelistState,
    PanelistStatus,
    PhaseEvent,
    PhaseEventData,
    SseEvent,
    StatusEvent,
    StatusEventData,
    SummaryOut,
    TypingEvent,
    TypingEventData,
)
from backend.stream import broker

logger = logging.getLogger(__name__)

#: 事件出口：SSE 端点接 ``broker.publish``，测试接 ``list.append``
Publish = Callable[[SseEvent], None]


async def _pause(seconds: float) -> None:
    """真人节奏的停顿。

    ``SPEAK_INTERVAL_SECONDS`` / ``THINK_INTERVAL_SECONDS`` 置 0 时**不进入事件循环**，
    保证测试与压测不被节奏拖慢（见 ``tests/conftest.py``）。
    """
    if seconds > 0:
        await asyncio.sleep(seconds)


class DiscussionNotFound(LookupError):
    """会话不存在（SSE 端点据此返回 404）。"""


def _load_experts(db, session: DiscussionSession) -> list[Expert]:
    """按契约顺序（主持人优先、其余按 id）读取席位。"""
    return (
        db.query(Expert)
        .filter(Expert.session_id == session.id)
        .order_by(Expert.is_host.desc(), Expert.id)
        .all()
    )


def _panelists(
    experts: list[Expert],
    *,
    speaking: Expert | None = None,
    state: PanelistState = PanelistState.SPEAKING,
    focus: str | None = None,
) -> list[PanelistStatus]:
    """渲染全部席位状态：只有当前发言人处于非 idle，且只有他公开 ``focus``。"""
    speaking_id = speaking.id if speaking is not None else None
    return [
        PanelistStatus(
            expert_id=expert.id,
            state=state if expert.id == speaking_id else PanelistState.IDLE,
            focus=focus if expert.id == speaking_id else None,
        )
        for expert in experts
    ]


def _save_message(
    db, session: DiscussionSession, expert: Expert, content: str
) -> Message:
    """发言落库（``round`` 同场递增唯一），返回可序列化的 ORM 对象。"""
    last_round = (
        db.query(func.max(Message.round))
        .filter(Message.session_id == session.id)
        .scalar()
        or 0
    )
    message = Message(
        session_id=session.id,
        expert_id=expert.id,
        content=content.strip() or "……",
        round=int(last_round) + 1,
    )
    db.add(message)
    db.commit()
    return message


def _items_of_type(
    db, session: DiscussionSession, item_type: ConsensusType
) -> list[ConsensusItem]:
    """读取某一类条目（整组替换语义，前端据此直接重绘一栏）。"""
    return (
        db.query(ConsensusItem)
        .filter(
            ConsensusItem.session_id == session.id,
            ConsensusItem.type == item_type.value,
        )
        .order_by(ConsensusItem.created_at, ConsensusItem.id)
        .all()
    )


def _message_count(db, session: DiscussionSession) -> int:
    return int(
        db.query(func.count(Message.id))
        .filter(Message.session_id == session.id)
        .scalar()
        or 0
    )


def _count_by_role(db, session: DiscussionSession, *, host: bool) -> int:
    """按「主持人 / 专家」分别统计已落库发言数（断点续播靠它判断说到哪了）。"""
    return int(
        db.query(func.count(Message.id))
        .join(Expert, Expert.id == Message.expert_id)
        .filter(Message.session_id == session.id, Expert.is_host.is_(host))
        .scalar()
        or 0
    )


class _Envelope:
    """事件序号与公共字段的唯一出口（``seq`` 从 1 起单调递增）。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._seq = 0

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def phase(
        self,
        phase: DiscussionPhase,
        *,
        round: int = 0,
        max_rounds: int | None = None,
    ) -> PhaseEvent:
        return PhaseEvent(
            session_id=self.session_id,
            seq=self._next(),
            data=PhaseEventData(
                phase=phase,
                round=max(0, round),
                max_rounds=max(1, int(max_rounds or settings.max_rounds)),
            ),
        )

    def typing(self, expert: Expert, *, round: int = 0, label: str = "正在发言") -> TypingEvent:
        """「谁正在发言」：前端据此在发言流顶部与专家卡片上同步高亮（回看不显示）。"""
        return TypingEvent(
            session_id=self.session_id,
            seq=self._next(),
            data=TypingEventData(
                expert_id=expert.id,
                expert_name=expert.name,
                round=max(0, round),
                label=label,
            ),
        )

    def status(
        self,
        panelists: list[PanelistStatus],
        *,
        session_status: SessionStatus = SessionStatus.LIVE,
    ) -> StatusEvent:
        return StatusEvent(
            session_id=self.session_id,
            seq=self._next(),
            data=StatusEventData(
                session_status=session_status, panelists=panelists
            ),
        )

    def message(self, message: Message) -> MessageEvent:
        return MessageEvent(
            session_id=self.session_id,
            seq=self._next(),
            data=MessageEventData(message=MessageOut.from_message(message)),
        )

    def consensus(
        self,
        item_type: ConsensusType,
        items: list[ConsensusItem],
        revision: int,
    ) -> ConsensusEvent:
        return ConsensusEvent(
            session_id=self.session_id,
            seq=self._next(),
            data=ConsensusEventData(
                type=item_type,
                items=[ConsensusItemOut.model_validate(item) for item in items],
                revision=revision,
            ),
        )

    def done(
        self,
        *,
        status: SessionStatus,
        total_rounds: int,
        message_count: int,
        summary: SummaryOut | None = None,
        degraded: bool = False,
    ) -> DoneEvent:
        return DoneEvent(
            session_id=self.session_id,
            seq=self._next(),
            data=DoneEventData(
                status=status,
                total_rounds=total_rounds,
                message_count=message_count,
                summary=summary,
                degraded=degraded,
            ),
        )


def _push_insights(
    db,
    session: DiscussionSession,
    envelope: _Envelope,
    publish: Publish,
    revision: int,
) -> None:
    """把某一版共识的两栏条目各推一次（整组替换语义，前端直接重绘）。"""
    for item_type in (ConsensusType.AGREE, ConsensusType.DISAGREE):
        items = _items_of_type(db, session, item_type)
        if items:
            publish(envelope.consensus(item_type, items, revision))


async def run_discussion(
    session_id: str,
    *,
    publish: Publish,
    client: LLMClient | None = None,
) -> None:
    """跑完一整场讨论；事件全部经 ``publish`` 输出（不直接接触 HTTP）。"""
    db = SessionLocal()
    envelope = _Envelope(session_id)
    llm = client or get_llm()
    every = max(1, settings.insight_every_n_messages)
    session: DiscussionSession | None = None
    rounds = 0

    try:
        session = db.get(DiscussionSession, session_id)
        if session is None:
            raise DiscussionNotFound(f"会话不存在：{session_id}")

        experts = _load_experts(db, session)
        host = next((expert for expert in experts if expert.is_host), None)
        guests = [expert for expert in experts if not expert.is_host]
        if host is None or not guests:
            raise RuntimeError(f"会话 {session_id} 阵容不完整（需 1 位主持人 + 至少 1 位专家）")
        by_id = {expert.id: expert for expert in experts}

        # ---- 开演：状态置 live，全员 idle 先上屏（断点续播时不再重放开场）
        session.status = SessionStatus.LIVE.value
        db.commit()
        publish(envelope.phase(DiscussionPhase.OPENING, round=0))
        publish(envelope.status(_panelists(experts)))

        # 断点续播：刷新 / 进程重启后，已经说过的段落不再重说一遍
        host_spoken = _count_by_role(db, session, host=True)
        debate_done = _count_by_role(db, session, host=False)
        opening_done = host_spoken > 0
        closing_done = host_spoken > 1

        # ---- 主持人开场（先「想一想」再开口，别一秒开场白就砸出来）
        if not opening_done:
            publish(
                envelope.status(
                    _panelists(
                        experts,
                        speaking=host,
                        state=PanelistState.PREPARING,
                        focus="介绍议题与在场嘉宾",
                    )
                )
            )
            publish(envelope.typing(host, round=0, label="正在开场"))
            await _pause(settings.typing_seconds)
            publish(
                envelope.status(
                    _panelists(experts, speaking=host, state=PanelistState.SPEAKING)
                )
            )
            await _pause(settings.think_interval_seconds)
            opening_text = await opening_flow.generate_opening(
                db, session, host, client=llm
            )
            publish(envelope.message(_save_message(db, session, host, opening_text)))
            publish(envelope.status(_panelists(experts)))
            await _pause(settings.speak_interval_seconds)

        # ---- 交锋：均衡轮转（每轮全员各说一次，主持人不上场）
        guest_names = [expert.name for expert in guests]
        schedule_plans = scheduling_flow.plan_schedule(
            guest_names,
            settings.max_rounds,
            rng=random.Random(f"{session.id}|schedule"),
        )
        rounds_total = max(1, len(schedule_plans))
        schedule: list[tuple[int, int, str]] = []
        for plan in schedule_plans:
            for position, name in enumerate(plan.order):
                schedule.append((plan.index, position, name))

        publish(envelope.phase(DiscussionPhase.DEBATE, round=0, max_rounds=rounds_total))
        last_speaker_id = host.id if not opening_done else (
            db.query(Message.expert_id)
            .filter(Message.session_id == session.id)
            .order_by(Message.round.desc())
            .limit(1)
            .scalar()
            or host.id
        )
        rounds = debate_done
        # 本场已用过的骨架（同一句式全场只出现一次）：断点续播时从空开始也能读通，
        # 因为句式只影响措辞，不影响事实与立场。
        used_skeletons: set[str] = set()

        for index, (round_no, position, forced_name) in enumerate(schedule, start=1):
            if index <= debate_done:
                continue
            # 每一轮开头发一次 phase：前端进度条按「轮」推进，而不是按发言条数
            if position == 0:
                publish(
                    envelope.phase(
                        DiscussionPhase.DEBATE, round=round_no, max_rounds=rounds_total
                    )
                )
            try:
                decision = turn_flow.select_next_speaker(
                    db,
                    session,
                    client=llm,
                    last_speaker_id=last_speaker_id,
                    forced_name=forced_name,
                )
            except Exception:  # noqa: BLE001 - 调度失败不允许中断整场
                logger.warning("举手调度失败，降级为轮流发言", exc_info=True)
                chosen = by_id.get(
                    next(
                        (expert.id for expert in guests if expert.name == forced_name),
                        guests[0].id,
                    )
                ) or guests[0]
                decision = turn_flow.TurnDecision(
                    expert_id=chosen.id,
                    expert_name=chosen.name,
                    intent="抢答",
                    reply_to_name="",
                    focus=turn_flow.DEFAULT_FOCUS,
                )

            speaker = by_id.get(decision.expert_id) or guests[0]
            publish(
                envelope.status(
                    _panelists(
                        experts,
                        speaking=speaker,
                        state=PanelistState.PREPARING,
                        focus=decision.focus,
                    )
                )
            )
            # 「正在发言」：先亮 typing（前端立刻出占位卡片），再等 1~2 秒开始吐字
            publish(envelope.typing(speaker, round=round_no))
            await _pause(settings.typing_seconds)
            publish(
                envelope.status(
                    _panelists(experts, speaking=speaker, state=PanelistState.SPEAKING)
                )
            )
            # 思考停顿：发言中状态要真的「看得见」，而不是瞬间闪一下
            await _pause(settings.think_interval_seconds)

            text = await speaking_flow.generate_speech(
                session,
                speaker,
                decision,
                turn=index,
                db=db,
                client=llm,
                used_skeletons=used_skeletons,
            )
            message = _save_message(db, session, speaker, text)
            rounds += 1
            publish(envelope.message(message))
            publish(envelope.status(_panelists(experts)))
            last_speaker_id = speaker.id
            # 换人间隔：下一位开口前留白，台词才不会像连珠炮
            await _pause(settings.speak_interval_seconds)

            if rounds % every == 0:
                try:
                    consensus_flow.extract_insights(db, session, client=llm)
                except Exception:  # noqa: BLE001 - 提炼失败不影响继续讨论
                    logger.warning("共识提炼失败，保留上一版", exc_info=True)
                else:
                    _push_insights(db, session, envelope, publish, rounds // every)

        # ---- 收尾：主持人总结（断点续播时，已经总结过的场次不再重说）
        publish(envelope.phase(DiscussionPhase.WRAP_UP, max_rounds=rounds_total))
        publish(
            envelope.status(
                _panelists(
                    experts,
                    speaking=host,
                    state=PanelistState.PREPARING,
                    focus="汇总共识与分歧",
                )
            )
        )
        if closing_done:
            # 上一轮已经总结完（只是进程没了），直接补 done，不重复生成
            summary = None
            final_status = SessionStatus.ENDED
        else:
            publish(envelope.typing(host, round=rounds_total, label="正在总结"))
            await _pause(settings.typing_seconds)
            await _pause(settings.think_interval_seconds)
            summary = await summary_flow.generate_summary(db, session, client=llm)
            publish(
                envelope.status(
                    _panelists(experts, speaking=host, state=PanelistState.SPEAKING)
                )
            )
            closing = _save_message(db, session, host, summary_flow.summary_text(summary))
            publish(envelope.message(closing))
            publish(envelope.status(_panelists(experts)))
            final_status = SessionStatus.ENDED

        session.status = SessionStatus.ENDED.value
        db.commit()
        publish(
            envelope.done(
                status=final_status,
                total_rounds=rounds,
                message_count=_message_count(db, session),
                summary=summary,
            )
        )
    except DiscussionNotFound:
        raise
    except Exception:  # noqa: BLE001 - 任何异常都必须给出终点事件
        logger.exception("讨论 %s 中途失败，走降级收尾", session_id)
        count = 0
        if session is not None:
            try:
                session.status = SessionStatus.ENDED.value
                db.commit()
                count = _message_count(db, session)
            except Exception:  # noqa: BLE001 - 收尾尽力而为
                db.rollback()
        publish(
            envelope.done(
                status=SessionStatus.ENDED,
                total_rounds=rounds,
                message_count=count,
                degraded=True,
            )
        )
    finally:
        db.close()


async def stream_events(
    session_id: str, *, client: LLMClient | None = None
) -> AsyncIterator[SseEvent]:
    """跑完一场讨论并把事件按序吐出来（测试 / 回放用；不经过广播中枢）。"""
    buffer: list[SseEvent] = []
    await run_discussion(session_id, publish=buffer.append, client=client)
    for event in buffer:
        yield event


# ---------------------------------------------------------------- 后台任务


#: 进程内讨论任务登记表：同一场只跑一次，重复连接只回放（``reset`` 供测试清理）
_TASKS: dict[str, "asyncio.Task[None]"] = {}


def _is_finished(session_id: str) -> bool:
    """会话是否已在库里结束（进程重启后防止把打完的场次再打一遍）。"""
    db = SessionLocal()
    try:
        session = db.get(DiscussionSession, session_id)
        return session is not None and session.status == SessionStatus.ENDED.value
    finally:
        db.close()


def _publish_finished(session_id: str) -> None:
    """已结束的会话：只补一个 ``done``，让客户端干净收尾。"""
    db = SessionLocal()
    try:
        session = db.get(DiscussionSession, session_id)
        if session is None:
            return
        envelope = _Envelope(session_id)
        rounds = int(
            db.query(func.max(Message.round))
            .filter(Message.session_id == session_id)
            .scalar()
            or 0
        )
        broker.publish(
            session_id,
            envelope.done(
                status=SessionStatus.ENDED,
                total_rounds=rounds,
                message_count=_message_count(db, session),
                degraded=False,
            ),
        )
    finally:
        db.close()


async def _run_and_close(session_id: str, client: LLMClient | None) -> None:
    """后台任务外壳：把事件写进广播中枢，无论成败都关闭通道。"""
    try:
        await run_discussion(
            session_id,
            publish=lambda event: broker.publish(session_id, event),
            client=client,
        )
    except DiscussionNotFound:
        logger.warning("会话 %s 不存在，无法开演", session_id)
    except Exception:  # noqa: BLE001
        logger.exception("讨论任务 %s 异常退出", session_id)
    finally:
        broker.close(session_id)


def start_discussion(
    session_id: str, *, client: LLMClient | None = None
) -> "asyncio.Task[None] | None":
    """确保该场讨论在后台推进（幂等）；已结束的会话只补 ``done``。"""
    task = _TASKS.get(session_id)
    if task is not None:
        return task
    if _is_finished(session_id):
        _publish_finished(session_id)
        return None

    task = asyncio.create_task(_run_and_close(session_id, client))
    _TASKS[session_id] = task
    return task


def reset_discussions() -> None:
    """清空任务登记表（测试 / 进程重启复用）。"""
    _TASKS.clear()


__all__ = [
    "DiscussionNotFound",
    "Publish",
    "reset_discussions",
    "run_discussion",
    "start_discussion",
    "stream_events",
]
