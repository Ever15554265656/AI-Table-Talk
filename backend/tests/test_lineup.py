"""TDD 用例：阵容生成落库（PRD AC-2.3 / AC-2.4 / AC-2.5 / AC-2.6）。

对应白盒要求：
- Mock LLM 返回固定 JSON（含主持人）→ 断言阵容落库且恰好一位主持人；
- 颜色必须来自服务端调色板、六位 hex、同场唯一；
- 阵容生成后保持 ``created``（等用户确认，不自动开演）。
"""

from __future__ import annotations

import re

from backend.config import PANEL_COLOR_PALETTE
from backend.flows.prompts import LINEUP_PROMPT
from backend.llm import SCENE_LINEUP
from backend.models import Expert, SessionStatus
from backend.services.lineup import generate_lineup
from backend.tests.helpers import seed_session

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_lineup_persists_one_host_plus_n_experts(db):
    """固定 JSON → 落库 1 位主持人 + N 位专家，字段与颜色满足契约。"""
    session = seed_session(db, topic="四天工作制是否真的提高了效率", expert_count=3)

    created = generate_lineup(db, session)

    rows = db.query(Expert).filter(Expert.session_id == session.id).all()
    assert len(created) == 4
    assert len(rows) == 4

    hosts = [row for row in rows if row.is_host]
    assert len(hosts) == 1, "阵容必须且只能有一位主持人"
    assert hosts[0].name and hosts[0].profession and hosts[0].title and hosts[0].stance

    assert all(row.name and row.profession and row.title and row.stance for row in rows)

    colors = [row.color for row in rows]
    assert all(HEX_COLOR.match(color) for color in colors)
    assert set(colors) <= set(PANEL_COLOR_PALETTE), "颜色必须由服务端调色板分配"
    assert len(set(colors)) == len(colors), "同场颜色必须唯一"

    names = [row.name for row in rows]
    assert len(set(names)) == len(names), "同场姓名必须唯一"

    # 阵容生成只建席位，不自动开演
    assert session.status == SessionStatus.CREATED.value


def test_lineup_stream_is_chunked(llm):
    """流式封装：同一份预设数据必须以多块 yield，供前端打字机消费。"""
    chunks = list(
        llm.stream(
            scene=SCENE_LINEUP,
            prompt=LINEUP_PROMPT,
            payload={
                "topic": "四天工作制是否真的提高了效率",
                "expert_count": 2,
                "palette": list(PANEL_COLOR_PALETTE),
            },
        )
    )

    text = "".join(chunks)
    assert len(chunks) > 5, "流式输出应被切成多个片段"
    assert text.strip().startswith("[")
    assert text.strip().endswith("]")
