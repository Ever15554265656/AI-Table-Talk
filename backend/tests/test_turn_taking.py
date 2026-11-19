"""TDD 用例：举手调度（PRD AC-3.2 / AC-3.3）。

两条硬约束：
1. 优先级必须是 **反驳 > 补充 > 抢答**（先看意图，再看发言次数，最后看席位顺序）；
2. 举手 / 调度属于内部决策，**不得写入 messages 表**（否则前端会把「想说话」当成「已发言」）。
"""

from __future__ import annotations

from backend.models import Message
from backend.services.turn_taking import INTENTS, build_turn_payload, select_next_speaker
from backend.tests.helpers import seed_messages


def test_refute_outranks_supplement_and_raise(db, llm, seeded):
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
    payload = build_turn_payload(db, seeded.session)
    refuter = payload["candidates"][2]["name"]
    before = db.query(Message).filter(Message.session_id == seeded.session.id).count()

    decision = select_next_speaker(db, seeded.session, client=llm)

    assert decision.intent == "反驳"
    assert decision.expert_name == refuter
    assert decision.expert_id in {expert.id for expert in seeded.guests}
    assert decision.reply_to_name in {expert.name for expert in seeded.everyone}
    assert decision.focus and len(decision.focus) <= 40

    after = db.query(Message).filter(Message.session_id == seeded.session.id).count()
    assert after == before, "调度不得写入 messages"
    assert db.query(Message).filter(Message.content.contains("举手")).count() == 0


class _SilentLLM:
    """返回空举手的最小桩：验证兜底不依赖模型输出。"""

    def complete(self, *, scene: str, prompt: str, payload: dict) -> str:
        assert scene == "turn_taking"
        return '{"raised_hands": []}'

    def stream(self, *, scene: str, prompt: str, payload: dict):
        yield self.complete(scene=scene, prompt=prompt, payload=payload)


def test_empty_hands_falls_back_to_least_spoken_guest(db, seeded):
    """模型没举手时，兜底挑「发言次数最少」的专家，且意图仍在三值内。"""
    seed_messages(db, seeded.session, [seeded.guests[1]], ["交付节奏一压缩，压力最后还是落到一线。"])

    decision = select_next_speaker(db, seeded.session, client=_SilentLLM())

    assert decision.intent in INTENTS
    assert decision.expert_name != seeded.guests[1].name
    assert decision.expert_name in {guest.name for guest in seeded.guests}
    assert decision.focus
