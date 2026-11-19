"""流程 6/6：收尾总结。

模型只负责「说人话」，结构由服务端兜住：
- 正文按**空行**切段，前三段依次对应「共识 / 分歧 / 结论建议」；
- 段数不足三段的，按实际段数返回并置 ``degraded=True``（不编造缺失段落）；
- 拼回正文 ``summary_text`` 供主持人收尾发言直接上屏。
"""

from __future__ import annotations

import re

from backend.flows.consensus import RECENT_LIMIT
from backend.flows.prompts import SUMMARY_PROMPT
from backend.llm import SCENE_SUMMARY, LLMClient, get_llm
from backend.models import ConsensusItem, DiscussionSession, Expert, Message
from backend.schemas import SummaryOut, SummarySection

#: 三个固定小标题（契约 ``SummarySection.heading`` 为 ``Literal``）
SUMMARY_HEADINGS: tuple[str, str, str] = ("共识", "分歧", "结论建议")

#: 送给模型的发言条数上限（整场 transcript 过长只会让模型抓不住重点）
TRANSCRIPT_LIMIT: int = 80

#: 单段正文上限（与 ``SummarySection.body`` 的 ``max_length`` 对齐）
BODY_MAX_LEN: int = 800

#: 模型彻底跑偏时的兜底段落
FALLBACK_BODY: str = "本次讨论没有留下可用的总结。"

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def build_summary_payload(
    session: DiscussionSession,
    messages: list[Message],
    experts: list[Expert],
    items: list[ConsensusItem],
) -> dict[str, object]:
    """组装总结所需的 user message：议题、完整 transcript、已积累的条目。"""
    by_id = {expert.id: expert for expert in experts}
    return {
        "topic": session.topic,
        "purpose": "summary",
        "transcript": [
            {
                "speaker": by_id[message.expert_id].name
                if message.expert_id in by_id
                else "",
                "content": message.content,
            }
            for message in messages[-TRANSCRIPT_LIMIT:]
        ],
        "insights": [
            {"type": item.type, "content": item.content} for item in items
        ],
    }


def split_summary_sections(raw: str) -> tuple[list[SummarySection], bool]:
    """把纯文本总结切成「共识 / 分歧 / 结论建议」，并判断是否需要标记降级。"""
    paragraphs = [
        paragraph.strip()
        for paragraph in _PARAGRAPH_SPLIT.split(raw.replace("\r\n", "\n"))
        if paragraph.strip()
    ]
    if not paragraphs:
        return (
            [
                SummarySection(
                    heading=SUMMARY_HEADINGS[-1], body=FALLBACK_BODY
                )
            ],
            True,
        )

    if len(paragraphs) > len(SUMMARY_HEADINGS):
        # 多出来的段落并进最后一段，避免丢掉信息又不超出三个标题
        head = paragraphs[: len(SUMMARY_HEADINGS) - 1]
        tail = "".join(paragraphs[len(SUMMARY_HEADINGS) - 1 :])
        paragraphs = [*head, tail]

    sections = [
        SummarySection(heading=heading, body=body[:BODY_MAX_LEN])
        for heading, body in zip(SUMMARY_HEADINGS, paragraphs, strict=False)
    ]
    return sections, len(sections) != len(SUMMARY_HEADINGS)


def summary_text(summary: SummaryOut) -> str:
    """把结构化总结拼回正文（主持人收尾发言直接用它上屏）。"""
    return "\n\n".join(section.body for section in summary.sections)


async def generate_summary(
    db,
    session: DiscussionSession,
    *,
    client: LLMClient | None = None,
) -> SummaryOut:
    """基于已落库的发言与条目生成整场总结。"""
    llm = client or get_llm()
    experts = (
        db.query(Expert)
        .filter(Expert.session_id == session.id)
        .order_by(Expert.is_host.desc(), Expert.id)
        .all()
    )
    messages = (
        db.query(Message)
        .filter(Message.session_id == session.id)
        .order_by(Message.round)
        .all()
    )
    items = (
        db.query(ConsensusItem)
        .filter(ConsensusItem.session_id == session.id)
        .order_by(ConsensusItem.created_at, ConsensusItem.id)
        .all()
    )

    raw = await llm.acomplete(
        scene=SCENE_SUMMARY,
        prompt=SUMMARY_PROMPT,
        payload=build_summary_payload(session, messages, experts, items),
    )
    sections, degraded = split_summary_sections(raw)
    return SummaryOut(
        session_id=session.id, sections=sections, degraded=degraded
    )


__all__ = [
    "BODY_MAX_LEN",
    "FALLBACK_BODY",
    "SUMMARY_HEADINGS",
    "TRANSCRIPT_LIMIT",
    "build_summary_payload",
    "generate_summary",
    "split_summary_sections",
    "summary_text",
]
