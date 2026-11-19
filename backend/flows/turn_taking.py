"""流程 2/6：举手调度（把「谁接着说」变成一个可测的纯函数）。

优先级规则（模型只负责举手，排序永远由服务端决定）：

1. 意图：``反驳 > 补充 > 抢答``；
2. 发言次数少的优先（保证冷场席位拿到话筒）；
3. 席位顺序（稳定兜底，避免同一票同分时抖动）。

硬约束：举手与调度属于**内部决策**，本模块只读 ``messages``，绝不写入。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func

from backend.flows.parsing import loads_json
from backend.flows.prompts import TURN_TAKING_PROMPT
from backend.llm import SCENE_TURN_TAKING, LLMClient, get_llm
from backend.models import DiscussionSession, Expert, Message

#: 三种可落地的发言意图（与 ``prompts.TURN_TAKING_PROMPT`` 的输出枚举一致）
INTENTS: tuple[str, ...] = ("抢答", "补充", "反驳")

#: 意图优先级：数值越大越靠前
INTENT_PRIORITY: dict[str, int] = {"反驳": 3, "补充": 2, "抢答": 1}

#: 公开关注点上限（契约 ``PanelistStatus.focus`` 为 60，这里留出余量）
FOCUS_MAX_LEN: int = 40

#: 送给模型的最近发言条数（过多只会稀释指令）
RECENT_TRANSCRIPT_LIMIT: int = 8

#: 模型没给关注点时的兜底文案（仍属公开信息，不是推理链）
DEFAULT_FOCUS: str = "想就本轮分歧补一个具体判断"


class TurnTakingError(RuntimeError):
    """无法调度（例如阵容里一个专家都没有）。"""


@dataclass(frozen=True)
class TurnDecision:
    """一次调度决策；只包含可公开字段。"""

    expert_id: str
    expert_name: str
    intent: str
    reply_to_name: str
    focus: str


@dataclass(frozen=True)
class _Hand:
    name: str
    intent: str
    reply_to: str
    focus: str


def _load_experts(db, session: DiscussionSession) -> list[Expert]:
    """按契约顺序（``is_host DESC, id ASC``）读取席位。"""
    return (
        db.query(Expert)
        .filter(Expert.session_id == session.id)
        .order_by(Expert.is_host.desc(), Expert.id)
        .all()
    )


def build_turn_payload(
    db,
    session: DiscussionSession,
    *,
    recent_limit: int = RECENT_TRANSCRIPT_LIMIT,
) -> dict[str, object]:
    """组装调度所需的运行期数据（身份一律用 ``name`` 表达，由服务端映射回 id）。"""
    experts = _load_experts(db, session)
    by_id = {expert.id: expert for expert in experts}

    recent = (
        db.query(Message)
        .filter(Message.session_id == session.id)
        .order_by(Message.round.desc())
        .limit(recent_limit)
        .all()
    )
    recent.reverse()

    counts = dict(
        db.query(Message.expert_id, func.count())
        .filter(Message.session_id == session.id)
        .group_by(Message.expert_id)
        .all()
    )
    last = recent[-1] if recent else None
    last_expert = by_id.get(last.expert_id) if last is not None else None

    return {
        "topic": session.topic,
        "candidates": [
            {
                "name": expert.name,
                "profession": expert.profession,
                "stance": expert.stance,
                "spoken_count": int(counts.get(expert.id, 0)),
            }
            for expert in experts
            if not expert.is_host
        ],
        "transcript": [
            {
                "speaker": by_id[message.expert_id].name
                if message.expert_id in by_id
                else "",
                "content": message.content,
            }
            for message in recent
        ],
        "last_speaker_name": last_expert.name if last_expert is not None else "",
    }


def _normalize_intent(value: object) -> str:
    """把模型输出的意图收敛到三值；无法判断时按「抢答」处理（最低优先级）。"""
    text = str(value or "").strip()
    if text in INTENTS:
        return text
    if "驳" in text or "反" in text:
        return "反驳"
    if "补" in text or "附" in text:
        return "补充"
    return "抢答"


def _parse_hands(raw: str) -> list[_Hand]:
    """解析举手 JSON；解析失败时返回空列表，交由兜底逻辑接手。"""
    try:
        data = loads_json(raw)
    except ValueError:
        return []
    items = data.get("raised_hands", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    hands: list[_Hand] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        hands.append(
            _Hand(
                name=name,
                intent=_normalize_intent(item.get("intent")),
                reply_to=str(item.get("reply_to") or "").strip(),
                focus=str(item.get("focus") or "").strip()[:FOCUS_MAX_LEN],
            )
        )
    return hands


def _fallback(pool: list[Expert], spoken: dict[str, int]) -> TurnDecision:
    """模型没举手（或全是不存在的姓名）时的兜底：发言次数最少者抢答。"""
    chosen = min(pool, key=lambda expert: spoken.get(expert.name, 0))
    return TurnDecision(
        expert_id=chosen.id,
        expert_name=chosen.name,
        intent="抢答",
        reply_to_name="",
        focus=DEFAULT_FOCUS,
    )


def select_next_speaker(
    db,
    session: DiscussionSession,
    *,
    client: LLMClient | None = None,
    last_speaker_id: str | None = None,
    recent_limit: int = RECENT_TRANSCRIPT_LIMIT,
) -> TurnDecision:
    """选出发言人；``last_speaker_id`` 用于避免同一人连着说两轮。"""
    llm = client or get_llm()
    experts = _load_experts(db, session)
    guests = [expert for expert in experts if not expert.is_host]
    if not guests:
        raise TurnTakingError(f"会话 {session.id} 没有专家席位，无法调度")

    pool = [expert for expert in guests if expert.id != last_speaker_id] or guests
    payload = build_turn_payload(db, session, recent_limit=recent_limit)
    allowed = {expert.name for expert in pool}
    candidates = [item for item in payload["candidates"] if item["name"] in allowed]
    payload["candidates"] = candidates

    spoken = {str(item["name"]): int(item["spoken_count"]) for item in candidates}
    order = {expert.name: index for index, expert in enumerate(pool)}
    known = {expert.name: expert for expert in pool}
    all_names = {expert.name for expert in experts}

    raw = llm.complete(
        scene=SCENE_TURN_TAKING, prompt=TURN_TAKING_PROMPT, payload=payload
    )
    ranked = [hand for hand in _parse_hands(raw) if hand.name in known]
    if not ranked:
        return _fallback(pool, spoken)

    # 意图 → 发言次数 → 席位顺序，三级排序保证结果稳定可复现
    ranked.sort(
        key=lambda hand: (
            -INTENT_PRIORITY[hand.intent],
            spoken[hand.name],
            order[hand.name],
        )
    )
    best = ranked[0]
    return TurnDecision(
        expert_id=known[best.name].id,
        expert_name=best.name,
        intent=best.intent,
        reply_to_name=best.reply_to if best.reply_to in all_names else "",
        focus=best.focus or DEFAULT_FOCUS,
    )


__all__ = [
    "DEFAULT_FOCUS",
    "FOCUS_MAX_LEN",
    "INTENTS",
    "INTENT_PRIORITY",
    "TurnDecision",
    "TurnTakingError",
    "build_turn_payload",
    "select_next_speaker",
]
