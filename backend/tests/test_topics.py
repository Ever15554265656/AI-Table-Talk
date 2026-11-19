"""TDD 用例：议题域规则（``backend/topics.py``）。

守住这一轮修的三件事 —— 它们在 Mock 侧就能完全验证：

1. **阵容跟着议题走**：职业来自议题域，而不是「固定职业池随机抽」；
2. **立场不一边倒**：同一场里 支持 / 反对 / 中立 同时出现，且立场文案句首带标签；
3. **发言咬住议题**：模板显式嵌入议题关键词，反驳指名上一位发言人，不出现「该议题」这类空指代。
"""

from __future__ import annotations

import json

import pytest

from backend import topics
from backend.config import PANEL_COLOR_PALETTE
from backend.llm import SCENE_LINEUP, mock_text

#: 三条验收议题 → 期望议题域 / 前 5 席职业 / 发言里必须出现的关键词
ACCEPTANCE: dict[str, dict[str, object]] = {
    "AI会不会取代程序员": {
        "label": "技术与互联网",
        "professions": (
            "技术专家",
            "伦理学者",
            "劳动经济学家",
            "科技行业投资人",
            "教育学者",
        ),
        "keywords": ("AI", "程序员"),
    },
    "该不该禁止中小学生带手机进校园": {
        "label": "教育与校园",
        "professions": (
            "一线教师",
            "教育学者",
            "学生代表",
            "家长代表",
            "政策研究员",
        ),
        "keywords": ("学生", "手机", "校园"),
    },
    "公司该不该推行四天工作制": {
        "label": "职场与组织",
        "professions": (
            "HR负责人",
            "企业创始人",
            "组织行为学者",
            "劳动法专家",
            "心理学教授",
        ),
        "keywords": ("公司", "四天", "工作"),
    },
}

#: 空指代黑名单：模板里一旦出现，发言就变成「不咬议题」的废话
EMPTY_REFERENCES: tuple[str, ...] = ("该议题", "这个问题", "本次讨论的话题")


@pytest.mark.parametrize("topic", list(ACCEPTANCE))
def test_detect_domain_follows_topic_text(topic: str) -> None:
    """议题域只由议题全文的关键词决定（同一句里出现几个域时取命中更多的那个）。"""
    domain = topics.detect_domain(topic)

    assert domain.label == ACCEPTANCE[topic]["label"]
    assert domain is not topics.FALLBACK_DOMAIN


def test_unknown_topic_falls_back_to_three_complementary_roles() -> None:
    """识别不出领域时，兜底域也必须给出「支持 / 反对 / 中立」三种互补角色。"""
    plans = topics.seat_plan("我们到底应当怎样看待这件事的长期影响", 3)

    assert [plan.stance_type for plan in plans] == ["支持", "反对", "中立"]
    assert len({plan.profession for plan in plans}) == 3


@pytest.mark.parametrize("topic", list(ACCEPTANCE))
def test_seat_plan_uses_domain_professions(topic: str) -> None:
    """专家数 ≤ 域内席位数时，职业按域内顺序取人 —— 不是随机抽。"""
    plans = topics.seat_plan(topic, len(ACCEPTANCE[topic]["professions"]))

    assert tuple(plan.profession for plan in plans) == ACCEPTANCE[topic]["professions"]



@pytest.mark.parametrize("topic", list(ACCEPTANCE))
def test_seat_plan_stance_is_labelled_and_spread(topic: str) -> None:
    """立场文案必须以标签开头（前端标签列直接取首字），且三种立场同时出现。"""
    plans = topics.seat_plan(topic, len(ACCEPTANCE[topic]["professions"]))
    labels = [topics.stance_label(plan.stance) for plan in plans]
    # 中立席位的句式是「有条件中立：…」，因此按模板前缀比对而不是纯标签
    prefixes = {"支持": "支持", "反对": "反对", "中立": "有条件中立"}

    assert all(plan.stance.startswith(prefixes[plan.stance_type]) for plan in plans)
    assert all(topics.stance_label(plan.stance) == plan.stance_type for plan in plans)
    assert {"支持", "反对", "中立"} <= set(labels)
    assert topics.stance_spread(labels) is True
    # 立场文案要能读出议题（否则「支持」支持的是什么看不出来）
    brief = topics.topic_brief(topic, 24)
    assert all(brief[:8] in plan.stance for plan in plans)


def test_seat_plan_flips_stance_when_experts_outnumber_seats() -> None:
    """专家数超过域内席位（6）时循环取人，立场翻转、论据换内核，不会出现两段同话。"""
    plans = topics.seat_plan("AI会不会取代程序员", 8)

    assert plans[0].profession == plans[6].profession
    assert plans[0].stance_type != plans[6].stance_type
    assert plans[0].claim != plans[6].claim
    assert len({plan.name for plan in plans}) == 8


@pytest.mark.parametrize("topic", list(ACCEPTANCE))
def test_mock_lineup_professions_come_from_domain(topic: str) -> None:
    """Mock 阵容（真实模式的同一套 payload）按议题域出人，且含主持人。"""
    seats = json.loads(
        mock_text(
            SCENE_LINEUP,
            {
                "topic": topic,
                "expert_count": len(ACCEPTANCE[topic]["professions"]),
                "palette": list(PANEL_COLOR_PALETTE),
            },
        )
    )

    assert len(seats) == len(ACCEPTANCE[topic]["professions"]) + 1
    assert [seat["profession"] for seat in seats[1:]] == list(
        ACCEPTANCE[topic]["professions"]
    )
    assert all(
        seat["stance"].startswith(("支持", "反对", "有条件中立")) for seat in seats[1:]
    )


@pytest.mark.parametrize("topic", list(ACCEPTANCE))
@pytest.mark.parametrize("intent", ["抢答", "补充", "反驳"])
def test_mock_speech_cites_topic_keywords(topic: str, intent: str) -> None:
    """三种意图、两套模板都必须引用议题中的真实关键词（不是「该议题」）。

    被回应的人取**第二位**专家：席位 0 本身就叫「林岚」，指回自己会变成自己反驳自己，
    这种自指在真实流程里不会出现（``reply_to`` 永远是上一位发言者）。
    """
    plans = topics.seat_plan(topic, 4)
    expected = ACCEPTANCE[topic]["keywords"]
    rival = plans[1].name

    for turn in (1, 2):
        text = mock_text(
            "speak",
            {
                "topic": topic,
                "purpose": "debate",
                "turn": turn,
                "speaker": plans[0].name,
                "profession": plans[0].profession,
                "stance": plans[0].stance,
                "intent": intent,
                "focus": plans[0].focus,
                "reply_to": rival,
            },
        )

        assert any(keyword in text for keyword in expected), text
        assert not any(phrase in text for phrase in EMPTY_REFERENCES), text
        assert len(text) <= 120, text
        # 不许自己反驳自己：发言人姓名不能出现在对自己的指称里
        assert plans[0].name not in text, text
        if intent in ("反驳", "补充"):
            assert rival in text, text
        else:
            assert rival not in text, text


@pytest.mark.parametrize("topic", list(ACCEPTANCE))
def test_mock_speech_uses_domain_claims_for_every_seat(topic: str) -> None:
    """每一位专家都能说出一句「像本行」的话：论据内核来自域内席位，不是通用套话。"""
    plans = topics.seat_plan(topic, 6)

    for index, plan in enumerate(plans):
        seat = topics.seat_for_profession(topic, plan.profession)
        assert seat is not None, plan.profession

        text = mock_text(
            "speak",
            {
                "topic": topic,
                "purpose": "debate",
                "turn": 1 + index % 2,
                "speaker": plan.name,
                "profession": plan.profession,
                "stance": plan.stance,
                "intent": "反驳",
                "focus": plan.focus,
                "reply_to": "周衡",
                # 第一次开口：机制理由应当是他自己的论据内核（0 = 第一次发言）
                "spoken_count": 0,
            },
        )

        assert any(core[:6] in text for core in seat.claims), text


def test_stance_label_tolerates_free_text_stance() -> None:
    """真实模型不一定写标签，``stance_label`` 仍要把自由文本归到三档。"""
    assert topics.stance_label("支持「四天工作制」：先改考核") == "支持"
    assert topics.stance_label("反对「四天工作制」：成本会转嫁") == "反对"
    assert topics.stance_label("有条件中立：「四天工作制」取决于考核口径") == "中立"
    assert topics.stance_label("我不认同这个方向，交付节奏会先崩") == "反对"
    assert topics.stance_label("") == "中立"
    # 回归：议题里带「该不该」时，中立立场不能被「不该」误判成反对
    assert topics.stance_label("有条件中立：「该不该禁手机」取决于执行细则") == "中立"


def test_stance_spread_rules() -> None:
    """「不一边倒」的判定：1 人放行、2 人不同即可、≥3 人必须支持与反对并存。"""
    assert topics.stance_spread(["支持"]) is True
    assert topics.stance_spread(["支持", "反对"]) is True
    assert topics.stance_spread(["支持", "支持"]) is False
    assert topics.stance_spread(["支持", "中立", "反对"]) is True
    assert topics.stance_spread(["支持", "中立", "支持"]) is False


def test_mock_speech_does_not_repeat_itself_across_turns() -> None:
    """同一位专家多次发言必须换角度（轮换论据内核与句式），不能复读同一句。"""
    topic = "公司该不该推行四天工作制"
    plan = topics.seat_plan(topic, 4)[0]
    base = {
        "topic": topic,
        "purpose": "debate",
        "speaker": plan.name,
        "profession": plan.profession,
        "stance": plan.stance,
        "intent": "反驳",
        "focus": plan.focus,
        "reply_to": "周衡",
    }

    # spoken_count 是主口径（本人第几次开口）；只给 turn 的老调用方退化为轮次
    by_count = [mock_text("speak", {**base, "turn": 4, "spoken_count": step}) for step in range(4)]
    by_turn = [mock_text("speak", {**base, "turn": turn}) for turn in (1, 2, 3, 4)]

    assert len(set(by_count)) == 4
    assert len(set(by_turn)) == 4
    assert all(text for text in by_count + by_turn)

