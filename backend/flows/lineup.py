"""流程 1/6：阵容生成（LLM → JSON → 校验 → 落库）。

三条规则：
1. 一次返回「主持人 + N 位专家」，**数组第 1 个对象即主持人**，服务端按位置置 ``is_host``；
2. 颜色由服务端按 ``PANEL_COLOR_PALETTE`` 顺序分配，忽略模型自带的颜色（防串色、保证同场唯一）；
3. 落库后状态保持 ``created``，等待用户确认 —— 阵容生成不等于开演。

Prompt 只负责「怎么选人、怎么输出 JSON」，本模块负责其余全部逻辑（校验、去重、配色、落库）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.config import PANEL_COLOR_PALETTE
from backend.flows.parsing import loads_json
from backend.flows.prompts import LINEUP_PROMPT
from backend.llm import SCENE_LINEUP, LLMClient, get_llm
from backend.models import DiscussionSession, Expert

#: 席位字段白名单：其余字段（含模型自报的 ``is_host``）一律丢弃
SEAT_FIELDS = ("name", "profession", "title", "stance", "color")


class LineupError(RuntimeError):
    """阵容结果不可用（数量不符 / 字段非法 / 重名）。"""


class _Seat(BaseModel):
    """单个席位的校验模型（长度上限与 ORM 列宽一致）。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=32)
    profession: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=64)
    stance: str = Field(min_length=1, max_length=200)
    #: 模型给的颜色只作参考，落库一律使用服务端调色板
    color: str | None = Field(default=None, max_length=7)


def _parse_seats(raw: str, expected: int) -> list[_Seat]:
    try:
        data = loads_json(raw)
    except ValueError as exc:
        raise LineupError(f"阵容不是合法 JSON：{exc}") from exc
    if not isinstance(data, list):
        raise LineupError("阵容必须是 JSON 数组")
    if len(data) != expected:
        raise LineupError(f"阵容应为 {expected} 个席位（含主持人），实际 {len(data)}")

    seats: list[_Seat] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise LineupError(f"第 {index + 1} 个席位不是 JSON 对象")
        try:
            seats.append(
                _Seat.model_validate(
                    {key: item.get(key) for key in SEAT_FIELDS if key in item}
                )
            )
        except ValueError as exc:
            raise LineupError(f"第 {index + 1} 个席位字段不合法：{exc}") from exc

    if len({seat.name for seat in seats}) != len(seats):
        raise LineupError("阵容出现重复姓名")
    return seats


def _palette_colors(count: int) -> list[str]:
    palette = list(PANEL_COLOR_PALETTE)
    if count > len(palette):
        raise LineupError(f"席位 {count} 超过调色板容量 {len(palette)}")
    return palette[:count]


def build_lineup_payload(session: DiscussionSession) -> dict[str, object]:
    """组装阵容生成的 user message 数据（议题 / 人数 / 调色板）。"""
    return {
        "topic": session.topic,
        "expert_count": session.expert_count,
        "palette": list(PANEL_COLOR_PALETTE),
    }


def generate_lineup(
    db, session: DiscussionSession, *, client: LLMClient | None = None
) -> list[Expert]:
    """生成并按 ``session_id`` 落库阵容，返回新建的席位（主持人排第一）。"""
    llm = client or get_llm()
    expected = session.expert_count + 1

    raw = llm.complete(
        scene=SCENE_LINEUP,
        prompt=LINEUP_PROMPT,
        payload=build_lineup_payload(session),
    )
    seats = _parse_seats(raw, expected)
    colors = _palette_colors(expected)

    # 重新生成语义：先清掉旧席位（created 阶段还没有发言，删除不会牵连 messages）
    db.query(Expert).filter(Expert.session_id == session.id).delete(
        synchronize_session=False
    )
    experts = [
        Expert(
            session_id=session.id,
            name=seat.name,
            profession=seat.profession,
            title=seat.title,
            stance=seat.stance,
            color=colors[index],
            # 位置即角色：数组第 1 个对象是主持人，避免模型自报 is_host 出错
            is_host=index == 0,
        )
        for index, seat in enumerate(seats)
    ]
    db.add_all(experts)
    db.commit()
    return experts


__all__ = [
    "LineupError",
    "SEAT_FIELDS",
    "build_lineup_payload",
    "generate_lineup",
]
