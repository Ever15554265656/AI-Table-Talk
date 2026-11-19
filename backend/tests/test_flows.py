"""TDD 用例：六个流程模块（``backend/flows/``）。

流程层只做「Prompt 之外的业务逻辑」：组装 user message、清洗模型输出、把结果收敛成契约模型。
这些断言因此都不碰 Prompt 文案本身（文案由 ``flows.prompts`` 的单测与审计负责）。

覆盖：
- ``opening``：主持人开场，剥离姓名前缀，专家名单不含主持人；
- ``speaking``：发言正文 ≤120 字、剥离「姓名：」前缀、payload 携带意图与关注点；
- ``summary``：三段文本 → 「共识 / 分歧 / 结论建议」，段数不足时置 ``degraded``。
"""

from __future__ import annotations

from backend.flows.opening import build_opening_payload, generate_opening
from backend.flows.speaking import (
    RECENT_SPEECH_LIMIT,
    SPEECH_MAX_LEN,
    build_speech_payload,
    count_own_speeches,
    generate_speech,
    load_recent_speeches,
)
from backend.flows.summary import (
    SUMMARY_HEADINGS,
    generate_summary,
    split_summary_sections,
    summary_text,
)
from backend.flows.turn_taking import TurnDecision
from backend.tests.helpers import seed_messages


class _VerboseLLM:
    """故意超长、且带「姓名：」前缀的桩，用来验证输出清洗。"""

    async def acomplete(self, *, scene: str, prompt: str, payload: dict) -> str:
        return f"{payload.get('speaker', '专家')}：" + "这是一段很长的发言。" * 20

    async def astream(self, *, scene: str, prompt: str, payload: dict):
        """发言走流式入口：这里一次性吐出全文即可。"""
        yield await self.acomplete(scene=scene, prompt=prompt, payload=payload)


class _SingleParagraphLLM:
    """只返回一段话的桩：总结必须降级而不是硬凑三个标题。"""

    async def acomplete(self, *, scene: str, prompt: str, payload: dict) -> str:
        return "这一场只谈了一件事。"


async def test_opening_excludes_host_from_guest_roster(db, llm, seeded):
    """开场 payload 的专家名单只含嘉宾，开场白不带姓名前缀。"""
    payload = build_opening_payload(seeded.session, seeded.host, seeded.everyone)

    assert payload["purpose"] == "opening"
    assert payload["speaker"] == seeded.host.name
    assert [item["name"] for item in payload["experts"]] == [
        guest.name for guest in seeded.guests
    ]
    assert all(item["profession"] for item in payload["experts"])

    text = await generate_opening(db, seeded.session, seeded.host, client=llm)
    assert text.strip()
    assert not text.startswith(seeded.host.name)
    assert not text.startswith("主持人：")


def test_speech_payload_carries_intent_focus_and_turn(db, seeded):
    """发言 payload 必须把「意图 / 关注点 / 回应对象」一起交给模型。"""
    decision = TurnDecision(
        expert_id=seeded.guests[0].id,
        expert_name=seeded.guests[0].name,
        intent="反驳",
        reply_to_name=seeded.guests[1].name,
        focus="先算清成本由谁承担",
    )

    payload = build_speech_payload(seeded.session, seeded.guests[0], decision, turn=3)

    assert payload["purpose"] == "debate"
    assert payload["turn"] == 3
    assert payload["speaker"] == seeded.guests[0].name
    assert payload["intent"] == "反驳"
    assert payload["reply_to"] == seeded.guests[1].name
    assert payload["focus"] == "先算清成本由谁承担"


async def test_speech_is_cleaned_and_capped(db, seeded):
    """超长 + 带前缀的模型输出必须被清洗成「一句人话」。"""
    decision = TurnDecision(
        expert_id=seeded.guests[1].id,
        expert_name=seeded.guests[1].name,
        intent="补充",
        reply_to_name="",
        focus="补一个前提",
    )

    speech = await generate_speech(
        seeded.session, seeded.guests[1], decision, turn=2, client=_VerboseLLM()
    )

    assert len(speech) <= SPEECH_MAX_LEN
    assert not speech.startswith(seeded.guests[1].name)
    assert "：" not in speech[: len(seeded.guests[1].name) + 1]


async def test_speech_uses_mock_pool_without_prefix(db, llm, seeded):
    """Mock 池的发言本身合规：非空且不超过长度上限。"""
    decision = TurnDecision(
        expert_id=seeded.guests[2].id,
        expert_name=seeded.guests[2].name,
        intent="抢答",
        reply_to_name="",
        focus="先问一句",
    )

    speech = await generate_speech(
        seeded.session, seeded.guests[2], decision, turn=1, client=llm
    )

    assert speech
    assert len(speech) <= SPEECH_MAX_LEN
    assert not speech.lstrip().startswith("{")


def test_split_summary_sections_maps_three_paragraphs():
    """三段文本按固定顺序映射为三个标题，且不算降级。"""
    raw = "第一段谈已经谈拢的判断。\n\n第二段谈真正对立的地方。\n\n第三段给出条件性结论。"

    sections, degraded = split_summary_sections(raw)

    assert [section.heading for section in sections] == list(SUMMARY_HEADINGS)
    assert [section.body for section in sections] == [
        "第一段谈已经谈拢的判断。",
        "第二段谈真正对立的地方。",
        "第三段给出条件性结论。",
    ]
    assert degraded is False


def test_split_summary_sections_degrades_on_short_output():
    """段数不足时只能降级，不能编造缺失的段落。"""
    sections, degraded = split_summary_sections("只有一段话。")

    assert degraded is True
    assert len(sections) == 1
    assert sections[0].heading == SUMMARY_HEADINGS[0]


async def test_generate_summary_reads_transcript_and_returns_sections(db, llm, seeded):
    """总结基于已落库的发言与条目生成，三个段落齐全且可拼回正文。"""
    seed_messages(
        db,
        seeded.session,
        [seeded.host, seeded.guests[0], seeded.guests[1]],
        [
            "先请两位说说，四天工作制到底改善了哪一段效率。",
            "在知识型岗位它确实减少了无效会议，但必须同步改考核方式。",
            "我不同意，交付节奏一压缩，压力最后还是落到一线。",
        ],
    )

    summary = await generate_summary(db, seeded.session, client=llm)

    assert summary.session_id == seeded.session.id
    assert [section.heading for section in summary.sections] == list(SUMMARY_HEADINGS)
    assert summary.degraded is False
    assert summary_text(summary).count("\n\n") == len(summary.sections) - 1


async def test_generate_summary_degrades_without_enough_paragraphs(db, seeded):
    """模型只给一段 → ``degraded=True``，仍返回可用结构。"""
    summary = await generate_summary(
        db, seeded.session, client=_SingleParagraphLLM()
    )

    assert summary.degraded is True
    assert summary.sections
    assert all(section.body for section in summary.sections)


def test_speech_payload_carries_recent_history(db, seeded):
    """发言 payload 必须带最近 3 条发言（按轮次升序）：没有上文，模型只能输出模板句。"""
    seed_messages(
        db,
        seeded.session,
        [seeded.guests[0], seeded.guests[1], seeded.guests[2], seeded.host],
        [
            "四天工作制先替代掉的是重复劳动，岗位会被重排。",
            "我不同意，交付节奏一压缩，人力并没有省下来。",
            "补一句：评审和沟通环节反而更吃人。",
            "先停一下，请各位把成本具体化。",
        ],
    )
    decision = TurnDecision(
        expert_id=seeded.guests[0].id,
        expert_name=seeded.guests[0].name,
        intent="反驳",
        reply_to_name=seeded.host.name,
        focus="把成本落在谁头上说清楚",
    )

    recent = load_recent_speeches(db, seeded.session)
    payload = build_speech_payload(
        seeded.session, seeded.guests[0], decision, turn=5, recent=recent
    )

    assert len(recent) == RECENT_SPEECH_LIMIT
    assert [item["speaker"] for item in recent] == [
        seeded.guests[1].name,
        seeded.guests[2].name,
        seeded.host.name,
    ]
    assert payload["recent"] == recent
    assert payload["recent"][-1]["content"].startswith("先停一下")


def test_speech_payload_carries_own_spoken_count(db, seeded):
    """payload 要带「本人已发言次数」：模型靠它避开复读自己上一轮。"""
    seed_messages(
        db,
        seeded.session,
        [seeded.guests[0], seeded.guests[1], seeded.guests[0]],
        [
            "工时不降、考核不改，四天制只会变成五天压成四天干。",
            "交付节奏一压缩，客户响应先崩。",
            "我先补一句：排班细则不写清楚，弹性就是空话。",
        ],
    )
    decision = TurnDecision(
        expert_id=seeded.guests[0].id,
        expert_name=seeded.guests[0].name,
        intent="补充",
        reply_to_name=seeded.guests[1].name,
        focus="把排班细则讲具体",
    )

    payload = build_speech_payload(
        seeded.session,
        seeded.guests[0],
        decision,
        turn=4,
        recent=load_recent_speeches(db, seeded.session),
        spoken_count=count_own_speeches(db, seeded.session, seeded.guests[0]),
    )

    assert payload["spoken_count"] == 2
    assert count_own_speeches(db, seeded.session, seeded.guests[2]) == 0


async def test_generate_speech_with_db_reads_history(db, llm, seeded):
    """传了 ``db`` 就带上历史；历史为空时不报错（开场后的第一句）。"""
    decision = TurnDecision(
        expert_id=seeded.guests[0].id,
        expert_name=seeded.guests[0].name,
        intent="抢答",
        reply_to_name="",
        focus="先给一个判断",
    )

    first = await generate_speech(
        seeded.session, seeded.guests[0], decision, turn=1, db=db, client=llm
    )
    assert first and len(first) <= SPEECH_MAX_LEN

    # 真实流程会把上一句落库：这里照样落库，第二次开口才有上文、也有 spoken_count
    seed_messages(
        db,
        seeded.session,
        [seeded.host, seeded.guests[0]],
        ["今天只讨论一个问题：成本由谁承担。", first],
    )
    second = await generate_speech(
        seeded.session, seeded.guests[0], decision, turn=2, db=db, client=llm
    )

    assert second and len(second) <= SPEECH_MAX_LEN
    assert second != first, "已经开过口之后不能复读自己上一句"

