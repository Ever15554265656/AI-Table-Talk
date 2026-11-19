"""流程 5/6：专家发言。

一次发言 = 「本人身份 + 本次意图 + 公开关注点 + 被回应的人」→ 1~2 句口语化正文。
本模块负责三件事：
1. 组装 user message（身份、意图、关注点、回应对象）；
2. 提供**流式**入口 ``stream_speech``（SSE 侧可用于打字机效果）；
3. 把模型输出洗成合规发言：去姓名前缀、去包裹引号、按句末标点截断到 120 字。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from backend.flows.parsing import clean_text
from backend.flows.prompts import SPEAKING_PROMPT
from backend.flows.turn_taking import TurnDecision
from backend.llm import SCENE_SPEAK, LLMClient, get_llm
from backend.models import DiscussionSession, Expert

#: 单次发言上限（PRD：一说就停，最多 2 句 / 120 字）
SPEECH_MAX_LEN: int = 120

#: 模型什么都没说时的兜底（宁可留白，也不许编造）
EMPTY_SPEECH: str = "……"

_SENTENCE_ENDS: tuple[str, ...] = ("。", "！", "？", "…", "!", "?")


def build_speech_payload(
    session: DiscussionSession,
    speaker: Expert,
    decision: TurnDecision,
    *,
    turn: int,
) -> dict[str, object]:
    """组装发言所需的 user message（意图与关注点必须一起给，否则模型会跑题）。"""
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
    client: LLMClient | None = None,
) -> AsyncIterator[str]:
    """流式产出模型原始片段（清洗在聚合之后做，保证截断以整句为单位）。"""
    llm = client or get_llm()
    async for chunk in llm.astream(
        scene=SCENE_SPEAK,
        prompt=SPEAKING_PROMPT,
        payload=build_speech_payload(session, speaker, decision, turn=turn),
    ):
        yield chunk


async def generate_speech(
    session: DiscussionSession,
    speaker: Expert,
    decision: TurnDecision,
    *,
    turn: int,
    client: LLMClient | None = None,
) -> str:
    """聚合流式结果并清洗成一条可上屏的发言。"""
    chunks = [
        chunk
        async for chunk in stream_speech(
            session, speaker, decision, turn=turn, client=client
        )
    ]
    return clean_speech("".join(chunks), speaker.name)


__all__ = [
    "EMPTY_SPEECH",
    "SPEECH_MAX_LEN",
    "build_speech_payload",
    "clean_speech",
    "generate_speech",
    "stream_speech",
]
