"""Pydantic 契约模型：REST 出入参 + SSE 六种事件。

本模块是**前后端唯一的契约真源**：前端类型由 ``/openapi.json`` 生成，
因此这里的字段命名、取值范围、约束即为接口协议本身（详见 ``docs/API.md``）。

约定：
- 输入模型统一 ``extra="forbid"``，多传字段直接 422；
- 输出模型统一 ``from_attributes=True``，便于 ``model_validate(orm_obj)``；
- 时间字段均为 UTC（ISO8601，无时区后缀）；
- 颜色统一 ``#RRGGBB``。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from backend.config import (
    DEFAULT_EXPERT_COUNT,
    EXPERT_COUNT_MAX,
    EXPERT_COUNT_MIN,
    TOPIC_MAX_LEN,
    TOPIC_MIN_LEN,
)
from backend.models import ConsensusType, SessionStatus, utc_now

if TYPE_CHECKING:  # 仅类型检查期导入，避免运行期循环依赖
    from backend.models import ConsensusItem, DiscussionSession, Expert, Message

HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
_WHITESPACE_RE = re.compile(r"\s+")


class ApiModel(BaseModel):
    """输出模型基类：允许直接从 ORM 对象校验。"""

    model_config = ConfigDict(from_attributes=True)


# ============================================================ REST 输出模型


class ExpertOut(ApiModel):
    """一个席位（主持人或专家）。"""

    id: str
    session_id: str
    name: str = Field(min_length=1, max_length=32, examples=["林岚"])
    profession: str = Field(min_length=1, max_length=64, examples=["组织行为学"])
    title: str = Field(min_length=1, max_length=64, examples=["某大学副教授"])
    stance: str = Field(
        min_length=1, max_length=200, examples=["远程办公削弱了偶发碰撞，创新产出显著下降"]
    )
    color: str = Field(pattern=HEX_COLOR_PATTERN, examples=["#F87171"])
    is_host: bool = Field(description="是否为主持人；主持人与专家同为可见角色")


class SessionListItem(ApiModel):
    """首页列表项。"""

    id: str
    topic: str
    expert_count: int
    status: SessionStatus
    created_at: datetime
    #: 以下两项由接口层填充（避免列表页 N+1 查询）
    expert_total: int = Field(default=0, ge=0, description="席位数，含主持人")
    message_count: int = Field(default=0, ge=0, description="已产生的可见发言数")


class SessionDetail(SessionListItem):
    """讨论详情（含完整阵容）。"""

    experts: list[ExpertOut] = Field(default_factory=list)


class LineupOut(BaseModel):
    """阵容快照：主持人 + 专家，供确认页与演播厅席位区渲染。"""

    session_id: str
    host: ExpertOut
    experts: list[ExpertOut] = Field(default_factory=list)
    total_seats: int = Field(ge=2, le=EXPERT_COUNT_MAX + 1)

    @classmethod
    def from_session(cls, session: "DiscussionSession") -> "LineupOut":
        host = next((expert for expert in session.experts if expert.is_host), None)
        if host is None:  # 数据完整性兜底：阵容缺失主持人视为异常数据
            raise ValueError(f"session {session.id} 缺少主持人席位")
        guests = [expert for expert in session.experts if not expert.is_host]
        return cls(
            session_id=session.id,
            host=ExpertOut.model_validate(host),
            experts=[ExpertOut.model_validate(expert) for expert in guests],
            total_seats=len(guests) + 1,
        )


class MessageOut(BaseModel):
    """一条可见发言（已打平发言人的展示字段，便于前端直接渲染）。"""

    id: str
    session_id: str
    expert_id: str
    expert_name: str
    expert_profession: str
    expert_title: str
    expert_color: str = Field(pattern=HEX_COLOR_PATTERN)
    content: str = Field(min_length=1)
    round: int = Field(ge=1)
    created_at: datetime

    @classmethod
    def from_message(cls, message: "Message") -> "MessageOut":
        expert: "Expert" = message.expert
        return cls(
            id=message.id,
            session_id=message.session_id,
            expert_id=message.expert_id,
            expert_name=expert.name,
            expert_profession=expert.profession,
            expert_title=expert.title,
            expert_color=expert.color,
            content=message.content,
            round=message.round,
            created_at=message.created_at,
        )


# ============================================================ REST 输入模型


class SessionCreate(BaseModel):
    """创建讨论请求。

    只接收「话题 + 专家人数」，其余（阵容、颜色、状态）一律由服务端生成，
    避免前端注入不合法阵容。
    """

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(
        min_length=TOPIC_MIN_LEN,
        max_length=TOPIC_MAX_LEN,
        description=f"讨论话题，{TOPIC_MIN_LEN}~{TOPIC_MAX_LEN} 字符（首尾空白会被裁剪）",
        examples=["远程办公是否降低了团队的创新力？"],
    )
    expert_count: int = Field(
        default=DEFAULT_EXPERT_COUNT,
        ge=EXPERT_COUNT_MIN,
        le=EXPERT_COUNT_MAX,
        description=f"专家人数（不含主持人），{EXPERT_COUNT_MIN}~{EXPERT_COUNT_MAX}",
        examples=[4],
    )

    @field_validator("topic")
    @classmethod
    def _normalize_topic(cls, value: str) -> str:
        normalized = _WHITESPACE_RE.sub(" ", value).strip()
        if len(normalized) < TOPIC_MIN_LEN:
            raise ValueError("话题不能为空")
        if len(normalized) > TOPIC_MAX_LEN:
            raise ValueError(f"话题长度不能超过 {TOPIC_MAX_LEN} 个字符")
        return normalized


class SessionRegenerate(BaseModel):
    """重新生成阵容（复用原话题，可选覆盖人数）。"""

    model_config = ConfigDict(extra="forbid")

    expert_count: int | None = Field(
        default=None,
        ge=EXPERT_COUNT_MIN,
        le=EXPERT_COUNT_MAX,
        description="不传则沿用创建时的人数",
    )


# ============================================================ 共识 / 分歧


class ConsensusItemOut(ApiModel):
    """一条共识或分歧。``expert_ids`` 已由服务端校验归属本场讨论。"""

    id: str
    session_id: str
    type: ConsensusType
    content: str = Field(min_length=1)
    expert_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class InsightsOut(BaseModel):
    """共识与分歧的整组快照（对应 SSE ``consensus`` 事件的数据结构）。"""

    session_id: str
    agree: list[ConsensusItemOut] = Field(default_factory=list)
    disagree: list[ConsensusItemOut] = Field(default_factory=list)


# ============================================================ 总结


class SummarySection(BaseModel):
    """总结的单个段落。``heading`` 被严格限制，避免模型自造结构。"""

    model_config = ConfigDict(extra="forbid")

    heading: Literal["共识", "分歧", "结论建议"]
    body: str = Field(min_length=1, max_length=800)


class SummaryOut(BaseModel):
    """主持人总结。前端只渲染 ``heading`` 与 ``body``，禁止序列化后上屏。"""

    session_id: str
    sections: list[SummarySection] = Field(default_factory=list)
    degraded: bool = Field(default=False, description="是否为降级兜底文案")
    created_at: datetime = Field(default_factory=utc_now)


# ============================================================ SSE 事件


class SseEventType(str, Enum):
    """六种事件类型（与前端 EventSource 监听名一一对应）。"""

    MESSAGE = "message"
    STATUS = "status"
    CONSENSUS = "consensus"
    PHASE = "phase"
    TYPING = "typing"
    DONE = "done"


class PanelistState(str, Enum):
    """专家运行状态（仅三态，PRD R4）。"""

    IDLE = "idle"
    PREPARING = "preparing"
    SPEAKING = "speaking"


class DiscussionPhase(str, Enum):
    """讨论阶段。"""

    OPENING = "opening"
    DEBATE = "debate"
    WRAP_UP = "wrap_up"


class PanelistStatus(BaseModel):
    """单个席位的实时状态。``focus`` 是**公开关注点**，不是推理链。"""

    model_config = ConfigDict(extra="forbid")

    expert_id: str
    state: PanelistState
    focus: str | None = Field(
        default=None, max_length=60, description="公开关注点（非 chain-of-thought）"
    )


class StatusEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_status: SessionStatus
    panelists: list[PanelistStatus] = Field(default_factory=list)


class MessageEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: MessageOut


class ConsensusEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ConsensusType
    items: list[ConsensusItemOut] = Field(default_factory=list)
    revision: int = Field(ge=1, description="第几次提炼结果，前端可据此丢弃乱序包")


class PhaseEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: DiscussionPhase
    round: int = Field(ge=0)
    max_rounds: int = Field(ge=1)


class TypingEventData(BaseModel):
    """「谁正在发言」的公开提示：前端据此在发言流顶部与专家卡片上同步高亮。

    只带发言人身份与轮次，不带任何正文 / 关注点推导过程 —— 正文仍由 ``message`` 事件给出。
    """

    model_config = ConfigDict(extra="forbid")

    expert_id: str
    expert_name: str
    round: int = Field(ge=0, description="当前轮次（0 表示开场 / 收尾阶段）")
    label: str = Field(
        default="正在发言", max_length=20, description="展示文案：正在发言 / 正在总结"
    )


class DoneEventData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SessionStatus
    total_rounds: int = Field(ge=0)
    message_count: int = Field(ge=0)
    summary: SummaryOut | None = None
    degraded: bool = False


class SseEventBase(BaseModel):
    """SSE 事件信封。``seq`` 同时作为 SSE ``id:``，支持 ``Last-Event-ID`` 重放。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    seq: int = Field(ge=1, description="会话内递增序号，即 SSE 事件 id")
    ts: datetime = Field(default_factory=utc_now)


class MessageEvent(SseEventBase):
    event: Literal["message"] = "message"
    data: MessageEventData


class StatusEvent(SseEventBase):
    event: Literal["status"] = "status"
    data: StatusEventData


class ConsensusEvent(SseEventBase):
    event: Literal["consensus"] = "consensus"
    data: ConsensusEventData


class PhaseEvent(SseEventBase):
    event: Literal["phase"] = "phase"
    data: PhaseEventData


class TypingEvent(SseEventBase):
    event: Literal["typing"] = "typing"
    data: TypingEventData


class DoneEvent(SseEventBase):
    event: Literal["done"] = "done"
    data: DoneEventData


#: 判别联合：可用 ``SseEventAdapter.validate_json(raw)`` 反序列化（重放 / 测试用）
SseEvent = Annotated[
    MessageEvent | StatusEvent | ConsensusEvent | PhaseEvent | TypingEvent | DoneEvent,
    Field(discriminator="event"),
]
SseEventAdapter: TypeAdapter[SseEvent] = TypeAdapter(SseEvent)

#: SSE 心跳帧（注释行，客户端忽略；用于保活与探测断线）
SSE_HEARTBEAT: str = ": ping\n\n"


def encode_sse(event: SseEvent) -> str:
    """把事件编码为 SSE 文本帧：``id`` + ``event`` + 单行 ``data``。"""
    payload = event.model_dump(mode="json")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.seq}\nevent: {event.event}\ndata: {body}\n\n"


def decode_sse_data(raw: str) -> SseEvent:
    """解析 ``data:`` 行携带的 JSON（用于重放与契约测试）。"""
    return SseEventAdapter.validate_json(raw)


__all__ = [
    "ConsensusEvent",
    "ConsensusEventData",
    "ConsensusItemOut",
    "DiscussionPhase",
    "DoneEvent",
    "DoneEventData",
    "ExpertOut",
    "InsightsOut",
    "LineupOut",
    "MessageEvent",
    "MessageEventData",
    "MessageOut",
    "PanelistState",
    "PanelistStatus",
    "PhaseEvent",
    "PhaseEventData",
    "SSE_HEARTBEAT",
    "SessionCreate",
    "SessionDetail",
    "SessionListItem",
    "SessionRegenerate",
    "SseEvent",
    "SseEventAdapter",
    "SseEventBase",
    "SseEventType",
    "StatusEvent",
    "StatusEventData",
    "SummaryOut",
    "SummarySection",
    "TypingEvent",
    "TypingEventData",
    "decode_sse_data",
    "encode_sse",
]
