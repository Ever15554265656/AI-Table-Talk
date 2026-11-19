"""ORM 模型：四张表。

- ``sessions``          一场圆桌讨论（话题 / 专家人数 / 状态）
- ``experts``           席位（主持人也落库，``is_host=True``，同为可见角色）
- ``messages``          **仅可见发言**（举手、调度决策等内部事件不落库）
- ``consensus_items``   共识 / 分歧条目（``type`` 区分）

隔离原则（PRD R9）：除主键外，所有子表都必须携带 ``session_id``；
数据访问层一律以 ``session_id`` 作为查询前缀条件，禁止跨会话读取。

时间约定：所有 ``*_at`` 字段均为 **UTC**（naive datetime），API 输出为 ISO8601。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


# ---------------------------------------------------------------- 领域枚举


class SessionStatus(str, Enum):
    """讨论状态。

    ``created`` 表示「阵容已生成、等待确认」；确认后进入 ``live``；
    结束并生成总结后为 ``ended``。
    """

    CREATED = "created"
    LIVE = "live"
    ENDED = "ended"


class ConsensusType(str, Enum):
    """共识 / 分歧类型。"""

    AGREE = "agree"
    DISAGREE = "disagree"


def utc_now() -> datetime:
    """统一的时间戳来源（UTC，naive，便于 SQLite 存取与比较）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    """32 位十六进制 UUID，用作主键。"""
    return uuid4().hex


STATUS_VALUES = ", ".join(f"'{item.value}'" for item in SessionStatus)
CONSENSUS_TYPE_VALUES = ", ".join(f"'{item.value}'" for item in ConsensusType)


# ---------------------------------------------------------------- 1. sessions


class DiscussionSession(Base):
    """一场圆桌讨论。"""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(f"status IN ({STATUS_VALUES})", name="ck_sessions_status"),
        CheckConstraint(
            "expert_count >= 2 AND expert_count <= 8", name="ck_sessions_expert_count"
        ),
        CheckConstraint("length(topic) >= 1", name="ck_sessions_topic_not_blank"),
        Index("ix_sessions_status", "status"),
        Index("ix_sessions_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 专家人数（**不含**主持人）
    expert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SessionStatus.CREATED.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    # --- 关系（父 → 子，删除父级时级联清除子记录，防止跨会话残留）---
    experts: Mapped[list["Expert"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: (Expert.is_host.desc(), Expert.id),
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: Message.round,
    )
    consensus_items: Mapped[list["ConsensusItem"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: ConsensusItem.created_at,
    )

    # ---------------------------------------------------------- 便捷访问器

    @property
    def host(self) -> "Expert | None":
        for expert in self.experts:
            if expert.is_host:
                return expert
        return None

    @property
    def guest_experts(self) -> list["Expert"]:
        return [expert for expert in self.experts if not expert.is_host]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<DiscussionSession id={self.id!r} status={self.status!r}>"


# ---------------------------------------------------------------- 2. experts


class Expert(Base):
    """一个席位（主持人 + 专家共用一张表）。"""

    __tablename__ = "experts"
    __table_args__ = (
        # 颜色必须是 6 位 hex（SQLite 无 REGEXP，用 length + substr 保证可移植）
        CheckConstraint(
            "length(color) = 7 AND substr(color, 1, 1) = '#'",
            name="ck_experts_color_hex",
        ),
        # 同一场讨论内颜色唯一（PRD AC-2.5）
        UniqueConstraint("session_id", "color", name="uq_experts_session_color"),
        # 同一场讨论内姓名唯一
        UniqueConstraint("session_id", "name", name="uq_experts_session_name"),
        Index("ix_experts_session", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    #: 职业，例如「组织行为学」
    profession: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 头衔，例如「某大学副教授」
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 立场（一句话，≤200 字）
    stance: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 席位专属颜色，六位 hex，例如 #F87171
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    #: 是否为主持人（主持人与专家同为可见角色，但职责不同）
    is_host: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped["DiscussionSession"] = relationship(back_populates="experts")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="expert",
        passive_deletes=True,
        order_by=lambda: Message.round,
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Expert {self.name!r} host={self.is_host}>"


# ---------------------------------------------------------------- 3. messages


class Message(Base):
    """一条**可见**发言。

    只保存会出现在 Transcript 上的内容：举手、抢答、调度决策等内部事件
    一律不落库（PRD R6 / R3）。``round`` 即发言序号，同场唯一。
    """

    __tablename__ = "messages"
    __table_args__ = (
        # 同一场讨论内轮次唯一：既是展示顺序，也是幂等写入的天然约束
        UniqueConstraint("session_id", "round", name="uq_messages_session_round"),
        Index("ix_messages_session_round", "session_id", "round"),
        Index("ix_messages_expert", "expert_id"),
        CheckConstraint("round >= 1", name="ck_messages_round_positive"),
        CheckConstraint("length(content) >= 1", name="ck_messages_content_not_blank"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    #: 发言人（主持人或专家）。消息必须归属某个席位，故不允许为空。
    expert_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("experts.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: 发言轮次（从 1 开始，同场递增且唯一）
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    session: Mapped["DiscussionSession"] = relationship(back_populates="messages")
    expert: Mapped["Expert"] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Message round={self.round} expert={self.expert_id!r}>"


# ---------------------------------------------------------- 4. consensus_items


class ConsensusItem(Base):
    """共识（``agree``）或分歧（``disagree``）条目。"""

    __tablename__ = "consensus_items"
    __table_args__ = (
        CheckConstraint(f"type IN ({CONSENSUS_TYPE_VALUES})", name="ck_consensus_type"),
        CheckConstraint("length(content) >= 1", name="ck_consensus_content_not_blank"),
        Index("ix_consensus_session_type", "session_id", "type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: 相关专家 id 列表（JSON 数组）。写入前必须校验归属本场讨论。
    expert_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    session: Mapped["DiscussionSession"] = relationship(back_populates="consensus_items")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<ConsensusItem type={self.type!r} content={self.content[:12]!r}>"


__all__ = [
    "ConsensusItem",
    "ConsensusType",
    "DiscussionSession",
    "Expert",
    "Message",
    "SessionStatus",
    "new_id",
    "utc_now",
]
