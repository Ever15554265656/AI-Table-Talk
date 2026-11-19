"""TDD 用例：举手调度（PRD AC-3.2 / AC-3.3）。

三条硬约束：

1. 优先级必须是 **反驳 > 补充 > 抢答**（先看意图，再看发言次数，最后才看随机序）；
2. 点人必须是**随机**的：席位顺序每轮打乱、同分席位按「立场对立」加权，
   不允许出现「1→2→3→4→1」这种一眼可见的循环；
3. 举手 / 调度属于内部决策，**不得写入 messages 表**（否则前端会把「想说话」当成「已发言」）。
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence

from backend.models import Message
from backend.services.turn_taking import (
    INTENTS,
    build_turn_payload,
    select_next_speaker,
)
from backend.tests.helpers import seed_messages


class _HandsLLM:
    """只回放预设举手的桩：把「模型说什么」与「服务端怎么排序」彻底解耦。

    只保留出现在 payload 候选里的举手，模拟真实模型「只对本轮候选举手」。
    """

    def __init__(self, hands: Sequence[dict[str, str]]) -> None:
        self.hands = list(hands)
        self.calls = 0

    def complete(self, *, scene: str, prompt: str, payload: dict) -> str:
        self.calls += 1
        names = {item["name"] for item in payload["candidates"]}
        hands = [hand for hand in self.hands if hand["name"] in names]
        return json.dumps({"raised_hands": hands}, ensure_ascii=False)

    def stream(self, *, scene: str, prompt: str, payload: dict):
        yield self.complete(scene=scene, prompt=prompt, payload=payload)


def test_refute_outranks_supplement_and_raise(db, seeded):
    """同一轮三人举手 → 选反驳者；举手事件不落库。"""
    seed_messages(
        db,
        seeded.session,
        [seeded.host, seeded.guests[0], seeded.guests[1]],
        [
            "今天只讨论一个问题：四天工作制是提高效率，还是把成本挪到别处。",
            "我认为在知识型岗位它确实减少了无效会议。",
            "我不同意，交付节奏一压缩，压力最后还是落到一线。",
        ],
    )
    stub = _HandsLLM(
        [
            {
                "name": seeded.guests[0].name,
                "intent": "抢答",
                "reply_to": "",
                "focus": "先把考核口径说清楚",
            },
            {
                "name": seeded.guests[2].name,
                "intent": "反驳",
                "reply_to": seeded.guests[1].name,
                "focus": "反对把成本直接转嫁给一线",
            },
        ]
    )
    before = db.query(Message).filter(Message.session_id == seeded.session.id).count()

    decision = select_next_speaker(
        db, seeded.session, client=stub, last_speaker_id=seeded.guests[1].id
    )

    assert decision.intent == "反驳"
    assert decision.expert_name == seeded.guests[2].name
    assert decision.expert_id in {expert.id for expert in seeded.guests}
    assert decision.reply_to_name == seeded.guests[1].name
    assert decision.focus and len(decision.focus) <= 40

    after = db.query(Message).filter(Message.session_id == seeded.session.id).count()
    assert after == before, "调度不得写入 messages"
    assert db.query(Message).filter(Message.content.contains("举手")).count() == 0


def test_rebuttal_without_target_names_previous_speaker(db, seeded):
    """模型给了「反驳」却没给对象 → 回填上一位发言人，不允许无指向的反驳。"""
    seed_messages(
        db,
        seeded.session,
        [seeded.guests[0]],
        ["AI 先吃掉的是重复编码，岗位会被重排，不是直接消失。"],
    )
    stub = _HandsLLM(
        [
            {
                "name": seeded.guests[2].name,
                "intent": "反驳",
                "reply_to": "",
                "focus": "反对把岗位变化说成岗位消失",
            }
        ]
    )

    decision = select_next_speaker(
        db, seeded.session, client=stub, last_speaker_id=seeded.guests[0].id
    )

    assert decision.intent == "反驳"
    assert decision.reply_to_name == seeded.guests[0].name


class _SilentLLM:
    """返回空举手的最小桩：验证兜底不依赖模型输出。"""

    def complete(self, *, scene: str, prompt: str, payload: dict) -> str:
        assert scene == "turn_taking"
        return '{"raised_hands": []}'

    def stream(self, *, scene: str, prompt: str, payload: dict):
        yield self.complete(scene=scene, prompt=prompt, payload=payload)


def test_empty_hands_falls_back_to_least_spoken_guest(db, seeded):
    """模型没举手时，兜底挑「发言次数最少」的专家，且意图仍在三值内。"""
    seed_messages(
        db, seeded.session, [seeded.guests[1]], ["交付节奏一压缩，压力最后还是落到一线。"]
    )

    decision = select_next_speaker(db, seeded.session, client=_SilentLLM())

    assert decision.intent in INTENTS
    assert decision.expert_name != seeded.guests[1].name
    assert decision.expert_name in {guest.name for guest in seeded.guests}
    assert decision.focus


def test_speaking_order_is_shuffled_not_a_seat_cycle(db, seeded):
    """连跑两圈：不许有人连说两轮，且顺序不能等于「按席位循环」。

    桩对所有候选举「抢答」（同意图、同发言次数），因此点人完全落在
    「席位洗牌 + 加权随机」上 —— 若回退成固定顺序，这里会立刻挂。
    """
    stub = _HandsLLM(
        [
            {"name": guest.name, "intent": "抢答", "reply_to": "", "focus": "想补一个判断"}
            for guest in seeded.guests
        ]
    )
    order = [guest.name for guest in seeded.guests]
    spoken: list[str] = []
    previous, last_id = seeded.host.name, seeded.host.id

    for round_index in range(2 * len(order)):
        decision = select_next_speaker(
            db,
            seeded.session,
            client=stub,
            last_speaker_id=last_id,
            rng=random.Random(round_index),
        )
        assert decision.expert_name != previous, "同一位专家不能连着说两轮"

        speaker = next(
            expert for expert in seeded.everyone if expert.id == decision.expert_id
        )
        seed_messages(
            db, seeded.session, [speaker], [f"第 {round_index + 1} 轮：{decision.intent}。"]
        )
        spoken.append(decision.expert_name)
        previous, last_id = decision.expert_name, decision.expert_id

    cycle = [order[index % len(order)] for index in range(len(spoken))]
    assert spoken != cycle, "发言顺序不能是固定的席位循环"
    assert len(set(spoken)) == len(order), "每个席位都应该拿到话筒"


def test_stance_opposite_raises_odds_of_being_picked(db, seeded):
    """立场与上一位相反的专家权重更高：多跑几十次，他应该明显更容易被点到。"""
    seed_messages(
        db, seeded.session, [seeded.host], ["今晚就一个议题：四天工作制到底是效率还是成本。"]
    )
    # 把第二位专家改成「反对」；上一位发言人（主持人）是「中立」
    seeded.guests[1].stance = "反对「四天工作制」：交付节奏会先崩"
    db.commit()

    stub = _HandsLLM(
        [
            {"name": guest.name, "intent": "抢答", "reply_to": "", "focus": "想补一个判断"}
            for guest in seeded.guests
        ]
    )
    picks: dict[str, int] = {}
    for round_index in range(60):
        decision = select_next_speaker(
            db, seeded.session, client=stub, rng=random.Random(round_index)
        )
        picks[decision.expert_name] = picks.get(decision.expert_name, 0) + 1

    assert picks.get(seeded.guests[1].name, 0) > picks.get(seeded.guests[0].name, 0)


def test_host_never_takes_the_floor(db, seeded):
    """主持人只开场 / 收尾：即便模型写了他的名字，也不会被调度成发言专家。"""
    stub = _HandsLLM(
        [{"name": seeded.host.name, "intent": "反驳", "reply_to": "", "focus": "控场"}]
    )

    decision = select_next_speaker(db, seeded.session, client=stub)

    assert decision.expert_name in {guest.name for guest in seeded.guests}




def test_first_hand_after_host_opening_is_a_raise(db, seeded):
    """主持人开场后没有「可反驳的观点」：指向主持人的反驳应降级为抢答。"""
    seed_messages(
        db, seeded.session, [seeded.host], ["今晚只讨论一个问题：成本到底由谁承担。"]
    )
    stub = _HandsLLM(
        [
            {
                "name": seeded.guests[0].name,
                "intent": "反驳",
                "reply_to": seeded.host.name,
                "focus": "先给一个自己的判断",
            }
        ]
    )

    decision = select_next_speaker(
        db, seeded.session, client=stub, last_speaker_id=seeded.host.id
    )

    assert decision.intent == "抢答"
    assert decision.reply_to_name == ""

