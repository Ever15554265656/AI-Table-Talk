"""测试数据工厂 + SSE 解析工具。

用例直接落库而不经过 REST 层：一方面让「阵容落库」「举手不落库」这类断言只依赖被测服务，
另一方面避免在服务层还没写全时把测试写成端到端大杂烩。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from backend.config import PANEL_COLOR_PALETTE
from backend.models import DiscussionSession, Expert, Message, SessionStatus, new_id
from backend.schemas import SseEvent, decode_sse_data

#: 主持人席位模板（与 ``prompts.py`` 的中立主持人设定一致）
HOST_SEAT: dict[str, str] = {
    "name": "沈知远",
    "profession": "财经媒体",
    "title": "资深圆桌主持人",
    "stance": "保持中立，只负责开场、追问与串联，不预设立场",
}

#: 专家席位模板；顺序即席位顺序，立场覆盖「支持 / 反对 / 有条件」
GUEST_SEATS: tuple[dict[str, str], ...] = (
    {
        "name": "林岚",
        "profession": "组织行为学",
        "title": "某高校副教授",
        "stance": "四天工作制只在知识型岗位成立，而且必须先改考核方式",
    },
    {
        "name": "周衡",
        "profession": "云计算",
        "title": "某云厂商首席架构师",
        "stance": "工时缩短会先压垮交付节奏，成本最后转嫁给一线",
    },
    {
        "name": "郑弈",
        "profession": "劳动法",
        "title": "某律所合伙人",
        "stance": "缺少工时与加班认定细则，企业会把它变成隐性加班",
    },
    {
        "name": "罗屿",
        "profession": "宏观经济学",
        "title": "某研究院研究员",
        "stance": "生产率提升是前提，否则只是把工资换成休息时间",
    },
)


@dataclass(frozen=True)
class Seeded:
    """一场已落库的讨论及其席位。"""

    session: DiscussionSession
    host: Expert
    guests: list[Expert]

    @property
    def everyone(self) -> list[Expert]:
        return [self.host, *self.guests]


def _palette_color(index: int) -> str:
    return PANEL_COLOR_PALETTE[index % len(PANEL_COLOR_PALETTE)]


def seed_session(
    db,
    *,
    topic: str,
    expert_count: int = 3,
    status: str = SessionStatus.CREATED.value,
) -> DiscussionSession:
    """建一条讨论记录（默认 ``created``，即「阵容待确认」）。"""
    session = DiscussionSession(
        id=new_id(), topic=topic, expert_count=expert_count, status=status
    )
    db.add(session)
    db.commit()
    return session


def seed_lineup(db, session: DiscussionSession) -> Seeded:
    """按调色板顺序补齐「主持人 + N 位专家」，颜色同场唯一。"""
    host = Expert(session_id=session.id, is_host=True, color=_palette_color(0), **HOST_SEAT)
    guests = [
        Expert(
            session_id=session.id,
            is_host=False,
            color=_palette_color(index + 1),
            **seat,
        )
        for index, seat in enumerate(GUEST_SEATS[: session.expert_count])
    ]
    db.add_all([host, *guests])
    db.commit()
    return Seeded(session=session, host=host, guests=guests)


def seed_messages(
    db,
    session: DiscussionSession,
    speakers: Iterable[Expert],
    contents: Iterable[str],
) -> list[Message]:
    """写入若干条可见发言，``round`` 从当前最大值继续递增。"""
    offset = db.query(Message).filter(Message.session_id == session.id).count()
    messages = [
        Message(
            session_id=session.id,
            expert_id=expert.id,
            content=text,
            round=offset + index,
        )
        for index, (expert, text) in enumerate(zip(speakers, contents, strict=True), start=1)
    ]
    db.add_all(messages)
    db.commit()
    return messages


def parse_sse_frames(raw: str) -> list[SseEvent]:
    """把整段 SSE 响应解析成契约模型列表。

    只依赖 ``data:`` 行（``id`` / ``event`` 已冗余进 JSON），心跳帧（``: ping``）自动跳过。
    """
    events: list[SseEvent] = []
    for frame in raw.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[len("data:") :].strip()
            for line in frame.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        events.append(decode_sse_data("\n".join(data_lines)))
    return events
