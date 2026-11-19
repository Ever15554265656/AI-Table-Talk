"""流程 2/6：举手调度（把「谁接着说」变成一个可测的纯函数）。

优先级规则（模型只负责举手，排序永远由服务端决定）：

1. 意图：``反驳 > 补充 > 抢答``；
2. 发言次数少的优先（保证冷场席位拿到话筒）；
3. **随机调度**：每轮先把席位顺序 ``shuffle`` 一遍，同分席位按「立场对立加权」随机点人
   （与上一位发言人立场相反的人更容易被点到，中立者次之，同立场者垫底）。

为什么第 3 条必须是随机的：固定按席位顺序排序，屏幕上就会出现肉眼可见的循环
（1→2→3→4→1…），观众一眼看出规律，辩论就假了。

硬约束：
* 举手与调度属于**内部决策**，本模块只读 ``messages``，绝不写入；
* ``last_speaker_id`` 保证「连续两轮不由同一位专家开头」；
* 反驳 / 补充没有有效回应对象时，回填上一位发言人 —— 不允许无指向的反驳。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func

from backend.flows.parsing import loads_json
from backend.flows.prompts import TURN_TAKING_PROMPT
from backend.llm import SCENE_TURN_TAKING, LLMClient, get_llm
from backend.models import DiscussionSession, Expert, Message
from backend import topics
from backend.topics import stance_label

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

#: 公平阈值：有人比最冷场席位多说了这么多轮时，强制把话筒交给最少发言的那组。
#: 目的：避免固定一两位专家霸麦、其余席位全程围观（意图优先级仍决定组内顺序）。
STARVATION_GAP: int = 2

#: 点名权重：与上一位发言人立场相反者 > 中立者 > 同立场者（越对立越容易吵起来）
STANCE_WEIGHT_OPPOSITE: float = 3.0
STANCE_WEIGHT_NEUTRAL: float = 1.5
STANCE_WEIGHT_SAME: float = 0.6


class _Random(Protocol):
    """只用到 ``shuffle`` / ``choice`` / ``choices`` 三个方法（便于测试注入固定种子）。"""

    def shuffle(self, x: list) -> None: ...

    def choice(self, seq): ...

    def choices(self, population, weights=None, *, k: int = 1): ...


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


def _fallback(pool: list[Expert], spoken: dict[str, int], *, rng=None) -> TurnDecision:
    """模型没举手（或全是不存在的姓名）时的兜底：发言次数最少者抢答（同分随机）。"""
    fewest = min(spoken.get(expert.name, 0) for expert in pool)
    choser = rng or random
    chosen = choser.choice(
        [expert for expert in pool if spoken.get(expert.name, 0) == fewest]
    )
    return TurnDecision(
        expert_id=chosen.id,
        expert_name=chosen.name,
        intent="抢答",
        reply_to_name="",
        focus=DEFAULT_FOCUS,
    )


def _stance_weights(
    names: list[str], stances: dict[str, str], last_stance: str
) -> list[float]:
    """立场对立加权：与上一位立场相反者最可能被点到，同立场者最低。"""
    weights: list[float] = []
    for name in names:
        label = stances.get(name) or "中立"
        if not last_stance:
            weights.append(1.0)
        elif label == last_stance:
            weights.append(STANCE_WEIGHT_SAME)
        elif label == "中立":
            weights.append(STANCE_WEIGHT_NEUTRAL)
        else:
            weights.append(STANCE_WEIGHT_OPPOSITE)
    return weights


def select_next_speaker(
    db,
    session: DiscussionSession,
    *,
    client: LLMClient | None = None,
    last_speaker_id: str | None = None,
    recent_limit: int = RECENT_TRANSCRIPT_LIMIT,
    rng=None,
    forced_name: str = "",
) -> TurnDecision:
    """选出发言人；``last_speaker_id`` 用于避免同一人连着说两轮（主持人开场后必换人）。

    ``forced_name`` 非空时表示「话筒已经按均衡轮转排给了这一位」：本函数不再挑人，
    只负责向他配上意图 / 回应对象 / 关注点（模型的举手结果若包含他，就沿用模型的意图）。
    ``rng`` 可注入 ``random.Random(seed)``，测试里用来固定随机序列。
    """
    choser = rng or random
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
    known = {expert.name: expert for expert in pool}
    all_names = {expert.name for expert in experts}
    host_names = {expert.name for expert in experts if expert.is_host}
    stances = {expert.name: stance_label(expert.stance) for expert in experts}
    last_name = str(payload.get("last_speaker_name") or "")
    last_stance = stances.get(last_name, "")

    raw = llm.complete(
        scene=SCENE_TURN_TAKING, prompt=TURN_TAKING_PROMPT, payload=payload
    )
    ranked = [hand for hand in _parse_hands(raw) if hand.name in known]
    # ---- 均衡轮转：话筒已经排给 forced_name，这里只负责给他配意图
    if forced_name and forced_name in known:
        target = known[forced_name]
        hand = next((item for item in ranked if item.name == forced_name), None)
        seat = topics.seat_for_profession(session.topic, target.profession)
        focus = (hand.focus if hand and hand.focus else (seat.focus if seat else DEFAULT_FOCUS))
        if hand is not None:
            intent = hand.intent
            reply_to = hand.reply_to if hand.reply_to in all_names else ""
        else:
            intent = "反驳" if last_name and last_name != forced_name else "抢答"
            reply_to = last_name if last_name and last_name != forced_name else ""
        if intent in ("反驳", "补充") and (not reply_to or reply_to in host_names):
            if last_name and last_name != forced_name and last_name not in host_names:
                reply_to = last_name
            else:
                reply_to = ""
                intent = "抢答"
        return TurnDecision(
            expert_id=target.id,
            expert_name=target.name,
            intent=intent,
            reply_to_name=reply_to,
            focus=focus or DEFAULT_FOCUS,
        )

    if not ranked:
        return _fallback(pool, spoken, rng=choser)

    # 公平兜底：发言次数差距过大时，只在「最少发言」的那组里选人，
    # 保证每个席位都能拿到话筒（组内仍按 意图 → 发言次数 → 随机序 排序）。
    # 注意判据用**整个候选池**：模型可能对冷场席位完全不给举手，
    # 只按举手结果过滤会让这些人永远轮不上（真实模型下尤其常见）。
    fewest = min(spoken.values())
    if max(spoken.values()) - fewest >= STARVATION_GAP:
        starved = [hand for hand in ranked if spoken[hand.name] == fewest]
        if not starved:
            return _fallback(pool, spoken, rng=choser)
        ranked = starved

    # 随机调度：每轮先把候选席位的顺序打乱，再用「立场对立加权」在同分席位里点人。
    # 固定按席位顺序排序会让发言顺序变成一眼可见的循环（1→2→3→4→1…）。
    shuffled = [expert.name for expert in pool]
    choser.shuffle(shuffled)
    rank_of = {name: index for index, name in enumerate(shuffled)}
    ranked.sort(
        key=lambda hand: (
            -INTENT_PRIORITY[hand.intent],
            spoken[hand.name],
            rank_of[hand.name],
        )
    )
    top = ranked[0]
    contenders = [
        hand
        for hand in ranked
        if INTENT_PRIORITY[hand.intent] == INTENT_PRIORITY[top.intent]
        and spoken[hand.name] == spoken[top.name]
    ]
    if len(contenders) == 1:
        best = top
    else:
        best = choser.choices(
            contenders,
            weights=_stance_weights([hand.name for hand in contenders], stances, last_stance),
            k=1,
        )[0]

    # 反驳 / 补充必须有指向：模型没给（或给了不存在的姓名）时回填上一位发言人，
    # 否则发言只能写成「有人觉得…」这类无指向的抬杠。
    # 主持人开场不算「可反驳的观点」：场上还没有专家发言时，降级为抢答（先给判断）。
    intent = best.intent
    reply_to = best.reply_to if best.reply_to in all_names else ""
    if intent in ("反驳", "补充") and (not reply_to or reply_to in host_names):
        if last_name and last_name != best.name and last_name not in host_names:
            reply_to = last_name
        else:
            reply_to = ""
            intent = "抢答"

    return TurnDecision(
        expert_id=known[best.name].id,
        expert_name=best.name,
        intent=intent,
        reply_to_name=reply_to,
        focus=best.focus or DEFAULT_FOCUS,
    )


__all__ = [
    "DEFAULT_FOCUS",
    "FOCUS_MAX_LEN",
    "INTENTS",
    "INTENT_PRIORITY",
    "STARVATION_GAP",
    "TurnDecision",
    "TurnTakingError",
    "build_turn_payload",
    "select_next_speaker",
]
