"""流程 5/6：专家发言。

一次发言 = 「本人身份 + 本次意图 + 公开关注点 + 被回应的人 + 近期上下文 + 本人已发言次数
+ 本场计划轮次 / 席位数量」→ 1~2 句口语化正文。
本模块负责四件事：
1. 组装 user message（身份、意图、关注点、回应对象、**最近 3 条历史发言**、**本人已发言次数**、
   **本场轮次规模**）；
2. 提供**流式**入口 ``stream_speech``（SSE 侧可用于打字机效果）；
3. 把模型输出洗成合规发言：去姓名前缀、去包裹引号、按句末标点截断到 120 字；
4. 只在有 ``db`` 时读历史（本模块对数据库只读，不写）。

为什么要带历史：只给「本人身份 + 意图」，模型只能输出通用模板句；带上最近 3 条发言，
它才能接住具体论点（「某某说岗位不会被抹掉，但…」），反驳也才有真实对象。
为什么要带「本人已发言次数」：同一位专家本场第二次开口时，若没有这个信号，
模型（以及 Mock）会把自己上一轮的话原样再说一遍。
为什么要带「轮次规模」：Mock 靠 ``turn`` / ``total_rounds`` / ``expert_count`` 判断当前处在
陈述、交锋、深化还是修正层，20 轮才会有「论断 → 交锋 → 修正 → 深化」的递进。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import func

from backend.config import settings
from backend.flows.parsing import clean_text
from backend.flows.prompts import SPEAKING_PROMPT
from backend.flows.turn_taking import TurnDecision
from backend.llm import SCENE_SPEAK, LLMClient, get_llm
from backend.models import DiscussionSession, Expert, Message

#: 单次发言上限（PRD：一说就停，最多 2 句 / 120 字）
SPEECH_MAX_LEN: int = 120

#: 发言时携带的历史发言条数（够模型接住上下文，又不至于把 Prompt 撑满）
RECENT_SPEECH_LIMIT: int = 3

#: 模型什么都没说时的兜底（宁可留白，也不许编造）
EMPTY_SPEECH: str = "……"

_SENTENCE_ENDS: tuple[str, ...] = ("。", "！", "？", "…", "!", "?")


def load_recent_speeches(
    db, session: DiscussionSession, *, limit: int = RECENT_SPEECH_LIMIT
) -> list[dict[str, str]]:
    """读取最近 ``limit`` 条发言（按轮次升序），供发言时衔接上文。只读，不写库。"""
    rows = (
        db.query(Message)
        .filter(Message.session_id == session.id)
        .order_by(Message.round.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "speaker": row.expert.name if row.expert is not None else "",
            "content": row.content,
        }
        for row in reversed(rows)
    ]


def count_own_speeches(db, session: DiscussionSession, speaker: Expert) -> int:
    """该专家在本场已经说过的条数（含开场白，用于「别复读自己上一轮」）。只读，不写库。"""
    return int(
        db.query(func.count())
        .select_from(Message)
        .filter(
            Message.session_id == session.id,
            Message.expert_id == speaker.id,
        )
        .scalar()
        or 0
    )


def build_speech_payload(
    session: DiscussionSession,
    speaker: Expert,
    decision: TurnDecision,
    *,
    turn: int,
    recent: list[dict[str, str]] | None = None,
    spoken_count: int = 0,
    used_skeletons: set[str] | None = None,
) -> dict[str, object]:
    """组装发言所需的 user message（意图 / 关注点 / 议题 / 最近发言必须一起给，否则会跑题）。"""
    return {
        "topic": session.topic,
        "purpose": "debate",
        "turn": turn,
        "speaker": speaker.name,
        "profession": speaker.profession,
        "title": speaker.title,
        "stance": speaker.stance,
        "intent": decision.intent,
        "focus": decision.focus,
        "reply_to": decision.reply_to_name,
        # 最近 3 条发言：让模型能接住具体论点，而不是输出模板句
        "recent": list(recent or []),
        # 本人已发言次数：0 表示第一次开口，>0 时不要复读自己上一轮
        "spoken_count": max(0, int(spoken_count)),
        # 本场计划轮次与席位数量：Mock 靠它判断「陈述 / 交锋 / 深化 / 修正」的层位置，
        # 真实模式也会拿到（模型据此知道现在是第几轮、该有多深）
        "total_rounds": max(1, int(settings.max_rounds or 20)),
        "expert_count": max(1, int(getattr(session, "expert_count", 0) or 4)),
        # 本场已用过的骨架：Mock 靠它保证「同一句式全场最多出现一次」（真实模式忽略）
        "used_skeletons": used_skeletons if used_skeletons is not None else set(),
    }


def _cap(text: str, limit: int) -> str:
    """按句末标点截断，避免把一句话切成半截。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for index in range(len(window) - 1, -1, -1):
        if window[index] in _SENTENCE_ENDS:
            return window[: index + 1].strip()
    return window.rstrip("，、；：,. ").strip()


def clean_speech(raw: str, speaker_name: str = "") -> str:
    """洗掉姓名前缀 / 包裹引号，并截断到 ``SPEECH_MAX_LEN``。"""
    prefixes: tuple[str, ...] = ()
    if speaker_name:
        prefixes = (f"{speaker_name}：", f"{speaker_name}:")
    cleaned = clean_text(raw, prefixes=prefixes)
    if not cleaned:
        return EMPTY_SPEECH
    return _cap(cleaned, SPEECH_MAX_LEN) or EMPTY_SPEECH


async def stream_speech(
    session: DiscussionSession,
    speaker: Expert,
    decision: TurnDecision,
    *,
    turn: int,
    db=None,
    client: LLMClient | None = None,
    used_skeletons: set[str] | None = None,
) -> AsyncIterator[str]:
    """流式产出模型原始片段（清洗在聚合之后做，保证截断以整句为单位）。

    传入 ``db`` 时会把最近 3 条历史发言一起送进 Prompt：没有上文，模型只能输出模板句。
    """
    llm = client or get_llm()
    recent = load_recent_speeches(db, session) if db is not None else []
    spoken_count = count_own_speeches(db, session, speaker) if db is not None else 0
    async for chunk in llm.astream(
        scene=SCENE_SPEAK,
        prompt=SPEAKING_PROMPT,
        payload=build_speech_payload(
            session,
            speaker,
            decision,
            turn=turn,
            recent=recent,
            spoken_count=spoken_count,
            used_skeletons=used_skeletons,
        ),
    ):
        yield chunk


async def generate_speech(
    session: DiscussionSession,
    speaker: Expert,
    decision: TurnDecision,
    *,
    turn: int,
    db=None,
    client: LLMClient | None = None,
    used_skeletons: set[str] | None = None,
) -> str:
    """聚合流式结果并清洗成一条可上屏的发言。"""
    chunks = [
        chunk
        async for chunk in stream_speech(
            session,
            speaker,
            decision,
            turn=turn,
            db=db,
            client=client,
            used_skeletons=used_skeletons,
        )
    ]
    return clean_speech("".join(chunks), speaker.name)


__all__ = [
    "EMPTY_SPEECH",
    "RECENT_SPEECH_LIMIT",
    "SPEECH_MAX_LEN",
    "build_speech_payload",
    "clean_speech",
    "count_own_speeches",
    "generate_speech",
    "load_recent_speeches",
    "stream_speech",
]
