"""流程 4/6：主持人开场。

开场的两件事都在这里：把「议题 + 席位表」拼成 user message，以及把模型输出洗成能直接上屏的一段话
（去掉「主持人：」这类前缀与包裹引号）。Prompt 本身在 ``flows.prompts.OPENING_PROMPT``。
"""

from __future__ import annotations

from backend.flows.parsing import clean_text
from backend.flows.prompts import OPENING_PROMPT
from backend.llm import SCENE_SPEAK, LLMClient, get_llm
from backend.models import DiscussionSession, Expert

#: 开场白长度上限（一段话，超过就该怀疑模型开始展开论证了）
OPENING_MAX_LEN: int = 200


def build_opening_payload(
    session: DiscussionSession, host: Expert, experts: list[Expert]
) -> dict[str, object]:
    """组装开场所需的 user message：议题、主持人、嘉宾席位（不含主持人）。"""
    return {
        "topic": session.topic,
        "purpose": "opening",
        "speaker": host.name,
        "experts": [
            {
                "name": expert.name,
                "profession": expert.profession,
                "title": expert.title,
                "stance": expert.stance,
            }
            for expert in experts
            if not expert.is_host
        ],
    }


def clean_opening(raw: str, host_name: str) -> str:
    """洗掉姓名 / 身份前缀并截断，保证屏幕上是「人说的话」。"""
    cleaned = clean_text(
        raw,
        prefixes=(f"{host_name}：", f"{host_name}:", "主持人：", "主持人:"),
    )
    if not cleaned:
        return "……"
    return cleaned[:OPENING_MAX_LEN].strip()


async def generate_opening(
    db,
    session: DiscussionSession,
    host: Expert,
    *,
    client: LLMClient | None = None,
) -> str:
    """生成主持人开场白（纯文本）。"""
    llm = client or get_llm()
    experts = (
        db.query(Expert)
        .filter(Expert.session_id == session.id)
        .order_by(Expert.is_host.desc(), Expert.id)
        .all()
    )
    raw = await llm.acomplete(
        scene=SCENE_SPEAK,
        prompt=OPENING_PROMPT,
        payload=build_opening_payload(session, host, experts),
    )
    return clean_opening(raw, host.name)


__all__ = [
    "OPENING_MAX_LEN",
    "build_opening_payload",
    "clean_opening",
    "generate_opening",
]
