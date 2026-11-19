"""流程 3/6：共识 / 分歧提炼（LLM → 条目 → 姓名映射 → 落库）。

两条防线：
1. **类型白名单**：只接受「共识 / 分歧」，映射为 ``models.ConsensusType``，其余整条丢弃；
2. **防幻觉**：模型返回的 ``speaker_names`` 必须能映射到本场真实席位，映射不到的姓名直接剔除；
   剔除后若一个发言人都不剩，则整条丢弃（宁缺毋滥）。

另外两条工程约定：
- 单条内容截断到 ``CONTENT_MAX_LEN``，避免卡片被一段话撑爆；
- 总量受 ``settings.max_consensus_items`` 约束，重复条目（同类型同内容）不再入库。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from backend.config import settings
from backend.flows.parsing import loads_json
from backend.flows.prompts import CONSENSUS_PROMPT
from backend.llm import SCENE_CONSENSUS, LLMClient, get_llm
from backend.models import ConsensusItem, ConsensusType, DiscussionSession, Expert, Message

#: 类型白名单：中文 / 英文别名统一收敛到 ORM 枚举值
TYPE_MAPPING: dict[str, str] = {
    ConsensusType.AGREE.value: ConsensusType.AGREE.value,
    "共识": ConsensusType.AGREE.value,
    "一致": ConsensusType.AGREE.value,
    "agreement": ConsensusType.AGREE.value,
    ConsensusType.DISAGREE.value: ConsensusType.DISAGREE.value,
    "分歧": ConsensusType.DISAGREE.value,
    "争议": ConsensusType.DISAGREE.value,
    "disagreement": ConsensusType.DISAGREE.value,
}

#: 单条内容上限（前端两栏卡片一行两句，超出就没人读）
CONTENT_MAX_LEN: int = 60

#: 提炼时附带的最近发言条数
RECENT_LIMIT: int = 12


class ConsensusError(RuntimeError):
    """提炼结果不可用（例如根本不是 JSON 数组）。"""


def _normalize_type(value: object) -> str | None:
    key = str(value or "").strip().lower()
    return TYPE_MAPPING.get(key) or TYPE_MAPPING.get(str(value or "").strip())


def _resolve_experts(
    names: Iterable[object], name_to_id: Mapping[str, str]
) -> list[str]:
    """把姓名映射为 ``expert_id``，保持出现顺序并去重（幻觉姓名自然被过滤）。"""
    resolved: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        expert_id = name_to_id.get(name)
        if expert_id and expert_id not in resolved:
            resolved.append(expert_id)
    return resolved


def build_consensus_payload(
    db,
    session: DiscussionSession,
    *,
    recent_limit: int = RECENT_LIMIT,
) -> dict[str, object]:
    """组装提炼所需的运行期数据：席位表、最近发言、已有条目。"""
    experts = (
        db.query(Expert).filter(Expert.session_id == session.id).order_by(Expert.id).all()
    )
    by_id = {expert.id: expert for expert in experts}

    recent = (
        db.query(Message)
        .filter(Message.session_id == session.id)
        .order_by(Message.round.desc())
        .limit(recent_limit)
        .all()
    )
    recent.reverse()
    existing = (
        db.query(ConsensusItem).filter(ConsensusItem.session_id == session.id).all()
    )

    return {
        "topic": session.topic,
        "experts": [
            {"name": expert.name, "profession": expert.profession} for expert in experts
        ],
        "messages": [
            {
                "speaker": by_id[message.expert_id].name
                if message.expert_id in by_id
                else "",
                "content": message.content,
            }
            for message in recent
        ],
        "existing_items": [
            {"type": item.type, "content": item.content} for item in existing
        ],
    }


def extract_insights(
    db,
    session: DiscussionSession,
    *,
    client: LLMClient | None = None,
    recent_limit: int = RECENT_LIMIT,
) -> list[ConsensusItem]:
    """提炼一次共识 / 分歧；返回本次**新写入**的条目（幂等：同内容不重复入库）。"""
    llm = client or get_llm()
    experts = (
        db.query(Expert).filter(Expert.session_id == session.id).order_by(Expert.id).all()
    )
    name_to_id = {expert.name: expert.id for expert in experts}
    existing = (
        db.query(ConsensusItem).filter(ConsensusItem.session_id == session.id).all()
    )

    raw = llm.complete(
        scene=SCENE_CONSENSUS,
        prompt=CONSENSUS_PROMPT,
        payload=build_consensus_payload(db, session, recent_limit=recent_limit),
    )
    try:
        data = loads_json(raw)
    except ValueError as exc:
        raise ConsensusError(f"提炼结果不是合法 JSON：{exc}") from exc
    if not isinstance(data, list):
        raise ConsensusError("提炼结果必须是 JSON 数组")

    budget = max(0, settings.max_consensus_items - len(existing))
    seen = {(item.type, item.content) for item in existing}
    created: list[ConsensusItem] = []

    for entry in data:
        if len(created) >= budget:
            break
        if not isinstance(entry, dict):
            continue
        item_type = _normalize_type(entry.get("type"))
        content = str(entry.get("content") or "").strip()[:CONTENT_MAX_LEN]
        if item_type is None or not content:
            continue
        if (item_type, content) in seen:
            continue
        expert_ids = _resolve_experts(entry.get("speaker_names") or (), name_to_id)
        if not expert_ids:
            continue

        item = ConsensusItem(
            session_id=session.id,
            type=item_type,
            content=content,
            expert_ids=expert_ids,
        )
        db.add(item)
        created.append(item)
        seen.add((item_type, content))

    if created:
        db.commit()
    return created


__all__ = [
    "CONTENT_MAX_LEN",
    "ConsensusError",
    "TYPE_MAPPING",
    "build_consensus_payload",
    "extract_insights",
]
