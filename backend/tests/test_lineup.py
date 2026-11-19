"""TDD 用例：阵容生成落库（PRD AC-2.3 / AC-2.4 / AC-2.5 / AC-2.6）。

对应白盒要求：
- Mock LLM 返回固定 JSON（含主持人）→ 断言阵容落库且恰好一位主持人；
- 颜色必须来自服务端调色板、六位 hex、同场唯一；
- 阵容生成后保持 ``created``（等用户确认，不自动开演）。
"""

from __future__ import annotations

import json
import re

from backend import topics
from backend.config import PANEL_COLOR_PALETTE
from backend.flows.prompts import LINEUP_PROMPT
from backend.llm import SCENE_LINEUP
from backend.models import Expert, SessionStatus
from backend.services.lineup import build_lineup_payload, generate_lineup
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


def test_lineup_payload_carries_topic_domain_and_keywords(db):
    """阵容 payload 必须带**完整议题** + 议题域 + 关键词（模型据此选角，Mock 也读同一份）。"""
    session = seed_session(db, topic="该不该禁止中小学生带手机进校园", expert_count=4)

    payload = build_lineup_payload(session)

    assert payload["topic"] == session.topic
    assert payload["domain"] == "教育与校园"
    assert payload["keywords"][:2] == ["学生", "手机"]
    assert payload["expert_count"] == 4


class _ScriptedLLM:
    """按脚本依次返回阵容 JSON 的桩：用来验证「立场一边倒 → 重试一次」。"""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.payloads: list[dict] = []

    def complete(self, *, scene: str, prompt: str, payload: dict) -> str:
        self.payloads.append(dict(payload))
        return self.replies[min(len(self.payloads) - 1, len(self.replies) - 1)]

    def stream(self, *, scene: str, prompt: str, payload: dict):
        yield self.complete(scene=scene, prompt=prompt, payload=payload)


def _seats_json(stances: list[str]) -> str:
    """拼一份「主持人 + N 位专家」的阵容 JSON（职业取域内席位，保证职业合法）。"""
    seats = [
        {
            "name": "沈知远",
            "profession": "财经媒体",
            "title": "资深圆桌主持人",
            "stance": "保持中立，只负责开场与串联",
        }
    ]
    for index, stance in enumerate(stances):
        seats.append(
            {
                "name": f"嘉宾{index + 1}",
                "profession": ("HR负责人", "企业创始人", "组织行为学者", "劳动法专家")[index],
                "title": "某公司相关负责人",
                "stance": stance,
            }
        )
    return json.dumps(seats, ensure_ascii=False)


def test_lineup_retries_once_when_all_experts_share_one_stance(db):
    """全员同一立场的阵容吵不起来 → 带强调重试一次，第二次结果直接采用。"""
    session = seed_session(db, topic="公司该不该推行四天工作制", expert_count=3)
    stub = _ScriptedLLM(
        [
            _seats_json(
                [
                    "支持「四天工作制」：效率会上去",
                    "支持「四天工作制」：员工更稳定",
                    "支持「四天工作制」：方向是对的",
                ]
            ),
            _seats_json(
                [
                    "支持「四天工作制」：效率会上去",
                    "反对「四天工作制」：交付节奏会先崩",
                    "有条件中立：「四天工作制」取决于考核口径",
                ]
            ),
        ]
    )

    created = generate_lineup(db, session, client=stub)

    assert len(stub.payloads) == 2, "一边倒时应重试且只重试一次"
    assert "stance_requirement" in stub.payloads[1]
    assert [topics.stance_label(expert.stance) for expert in created[1:]] == [
        "支持",
        "反对",
        "中立",
    ]

