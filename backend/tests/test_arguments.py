"""TDD 用例：``backend/arguments.py`` Mock 论证合成。

验收口径（本轮 5 条）：
1. **咬住议题**：每条发言都出现议题关键词，并正面回答议题问法；
2. **零旧素材**：不再内置任何具体议题的案例（手机登记、67% 教师调研、3 亿岗位…），
   换议题不串题；
3. **反驳不复读**：交锋只复述对方「关键判断词」（≤10 字），不出现整句照搬；
4. **落到具体层面**：每条发言至少带一个场景 / 时间线 / 结构性数字锚点；
5. **不重样**：同席位不重复、跨席位整句不撞车、四层齐全、收尾只做有条件让步。

用例只调纯函数（不建库、不起服务），跑一遍覆盖全部议题域 + 兜底域。
"""

from __future__ import annotations

import json
import re

import pytest

from backend import arguments, topics
from backend.llm import mock_text

#: 覆盖 5 个具名域 + 兜底域；含本轮两个验收议题
TOPICS: tuple[str, ...] = (
    "AI的发展对孩子的教育而言是否有利？",  # education
    "该不该降低法定结婚年龄",  # general（兜底域）
    "该不该禁止中小学生带手机进校园？",  # education
    "四天工作制是否值得推广？",  # workplace
    "城市该不该继续限行？",  # city
    "该不该强制企业披露碳排放数据？",  # tech
)

#: 旧素材黑名单：这些字符串一旦出现，说明又回到了「把某个议题写死」的老路
OLD_MATERIALS: tuple[str, ...] = (
    "67%",
    "高盛",
    "3亿岗位",
    "欧盟AI法案",
    "法国2018",
    "伦敦2003",
    "北京2019年路网饱和度",
    "某市地铁工期",
    "课间偷用",
    "每班每天登记",
    "关机测试",
    "GPT-3",
)

#: 修正层的标志词：让步（我认 / 答得不够）或守判断（我不改 / 我不退）
CONCESSION_MARKS: tuple[str, ...] = (
    "我认",
    "确实没说清",
    "我保留",
    "我不改",
    "我不退",
    "答得不够",
)


class Turn:
    """一条模拟发言：谁说的、第几轮、哪一层、对着谁说的什么。"""

    __slots__ = ("name", "turn", "phase", "content", "opponent", "opponent_content")

    def __init__(
        self,
        name: str,
        turn: int,
        phase: str,
        content: str,
        opponent: str = "",
        opponent_content: str = "",
    ) -> None:
        self.name = name
        self.turn = turn
        self.phase = phase
        self.content = content
        self.opponent = opponent
        self.opponent_content = opponent_content


def transcript(topic: str, expert_count: int = 5) -> list[Turn]:
    """按真实流程跑一遍：每次只由一位专家发言，``recent`` 取最近 4 条。

    轮次数与 ``discussion`` 的默认盘面一致（每人约 3 轮 + 收尾），
    发言顺序就是阵容顺序，这样「同阵营两人接话」的场景也能覆盖到。
    """
    plan = topics.seat_plan(topic, expert_count)
    total_rounds = expert_count * 3 + 2
    history: list[dict[str, str]] = []
    turns: list[Turn] = []
    used: set[str] = set()
    for turn in range(1, total_rounds + 1):
        seat = plan[(turn - 1) % len(plan)]
        opponent = history[-1]["speaker"] if history else ""
        opponent_content = history[-1]["content"] if history else ""
        content = arguments.compose_speech(
            topic=topic,
            intent="反驳" if turn % 2 == 0 else "抢答",
            speaker=seat.name,
            profession=seat.profession,
            stance=seat.stance,
            reply_to=opponent if turn % 2 == 0 else "",
            recent=history[-4:],
            step=turn - 1,
            turn=turn,
            total_rounds=total_rounds,
            expert_count=len(plan),
            used_skeletons=used,
        )
        history.append({"speaker": seat.name, "content": content})
        turns.append(
            Turn(
                seat.name,
                turn,
                arguments.phase_for(turn, total_rounds, len(plan)),
                content,
                opponent,
                opponent_content,
            )
        )
    return turns


@pytest.mark.parametrize("topic", TOPICS)
def test_speech_sticks_to_topic(topic):
    """标准 1：每条发言都要念到议题关键词（不是「该议题」这类空指代）。"""
    keyword = arguments.primary_keyword(topic)
    missing = [item.content for item in transcript(topic) if keyword not in item.content]

    assert not missing, missing


@pytest.mark.parametrize("topic", TOPICS)
def test_no_old_topic_material_leaks(topic):
    """标准 2：旧议题素材一条都不许再出现（换题不串题）。"""
    leaks = [
        f"{item.name}: {old}"
        for item in transcript(topic)
        for old in OLD_MATERIALS
        if old in item.content
    ]

    assert not leaks, leaks


def test_ai_education_topic_speaks_about_learning_not_phones():
    """验收议题 1：AI 对孩子教育，必须谈学习机制，且零手机内容。"""
    topic = "AI的发展对孩子的教育而言是否有利？"
    joined = "".join(item.content for item in transcript(topic, 4))

    assert "教育" in joined
    assert any(word in joined for word in ("学习", "课堂", "思考", "评价", "分层"))
    for banned in ("手机", "禁令", "带手机", "登记"):
        assert banned not in joined, banned


def test_marriage_age_topic_does_not_borrow_education_material():
    """验收议题 2：兜底域议题不许借教育 / 技术域的素材。"""
    topic = "该不该降低法定结婚年龄"
    joined = "".join(item.content for item in transcript(topic, 4))

    assert "降低法定结婚年龄" in joined
    for banned in ("课堂", "作业", "模型", "教师", "手机"):
        assert banned not in joined, banned


@pytest.mark.parametrize("topic", TOPICS)
def test_every_speech_is_grounded(topic):
    """标准 4：每条发言至少落到一个场景 / 时间线 / 结构性数字锚点。"""
    anchors = set()
    for seat in topics.seat_plan(topic, 5):
        pack = arguments.argument_pack(topic, seat.profession, seat.stance)
        anchors.update(pack.evidence)
        anchors.add(pack.scene)
    empty = [
        item.content
        for item in transcript(topic)
        if not any(anchor and anchor in item.content for anchor in anchors)
    ]

    assert not empty, empty


@pytest.mark.parametrize("topic", TOPICS)
def test_same_seat_never_repeats_verbatim(topic):
    """标准 2：同一位专家前后几轮的发言不能一字不差。"""
    seen: dict[str, list[str]] = {}
    repeated: list[str] = []
    for item in transcript(topic):
        if item.content in seen.get(item.name, []):
            repeated.append(f"{item.name} 第{item.turn}轮")
        seen.setdefault(item.name, []).append(item.content)

    assert not repeated, repeated


@pytest.mark.parametrize("topic", TOPICS)
def test_clash_paraphrases_key_claim_and_names_opponent(topic):
    """标准 3：交锋复述「关键判断词」（≤10 字）+ 点名，不整句照搬。"""
    clashes = [item for item in transcript(topic) if "「" in item.content]

    assert len(clashes) >= 2, "整场至少要有两次交锋"
    for item in clashes:
        key = item.content.split("「", 1)[1].split("」", 1)[0]
        assert 1 <= len(key) <= arguments.KEY_CLAIM_LIMIT, key
        assert item.opponent and item.opponent in item.content, item.content
        # 除引号内那句判断词外，反驳不得与对方原句有更长的重合片段
        rest = item.content.replace(f"「{key}」", "")
        if item.opponent_content:
            assert len(_longest_common(rest, item.opponent_content)) <= arguments.KEY_CLAIM_LIMIT


def _longest_common(left: str, right: str) -> str:
    """最长公共子串（用于判定「是否照搬了对方原句」）。"""
    best = ""
    for start in range(len(left)):
        end = start + len(best) + 1
        while end <= len(left) and left[start:end] in right:
            best = left[start:end]
            end += 1
    return best


@pytest.mark.parametrize("topic", TOPICS)
def test_layers_all_appear_and_correction_concedes(topic):
    """标准 4：四层都要出现；收尾两轮必须有让步 / 守判断的修正层。"""
    turns = transcript(topic)
    phases = {item.phase for item in turns}

    assert phases == {
        arguments.PHASE_STATEMENT,
        arguments.PHASE_CLASH,
        arguments.PHASE_DEEPEN,
        arguments.PHASE_CORRECTION,
    }
    last = turns[-2:]
    for item in last:
        assert item.phase == arguments.PHASE_CORRECTION
        assert any(mark in item.content for mark in CONCESSION_MARKS), item.content


@pytest.mark.parametrize("topic", TOPICS)
def test_no_sentence_is_shared_across_seats(topic):
    """标准 5：同一句整句不能出现在两个人的嘴里。"""
    owner: dict[str, str] = {}
    shared: list[str] = []
    for item in transcript(topic):
        for sentence in item.content.split("。"):
            piece = sentence.strip()
            if len(piece) < 12:
                continue
            if piece in owner and owner[piece] != item.name:
                shared.append(f"{owner[piece]} / {item.name}：{piece}")
            owner.setdefault(piece, item.name)

    assert not shared, shared


def test_key_claim_is_short_and_never_copies_a_whole_sentence():
    """``key_claim``：只取判断词，不搬整句。"""
    content = "该不该禁止中小学生带手机进校园：不该一刀切，课堂注意力确实会被打断。"
    key = arguments.key_claim(content)

    assert 2 <= len(key) <= arguments.KEY_CLAIM_LIMIT
    assert len(content) > len(key) * 3
    assert arguments.key_claim("") == ""


def test_skeleton_catalog_meets_minimum_variants():
    """标准 3（问题 4）：五类骨架每类 ≥5 条，组合句式 ≥20 种。"""
    for kind in ("statement", "reason", "evidence", "clash", "concession"):
        heads, tails = arguments.SKELETON_KINDS[kind]
        assert len(heads) >= 5, kind
        assert len(tails) >= 4, kind
        assert len(heads) * len(tails) >= 20, kind


def test_domains_speak_with_their_own_deadlock_and_ground():
    """共识前提 / 死结按域取材：不同域不该共用同一句兜底。"""
    domains = {topics.detect_domain(topic).key for topic in TOPICS}
    deadlocks = {arguments.deadlock_for(topic) for topic in TOPICS}
    grounds = {arguments.common_ground_for(topic) for topic in TOPICS}

    assert len(domains) >= 4
    assert len(deadlocks) == len(domains)
    assert len(grounds) == len(domains)


# ------------------------------------------------------------------ 共识 / 总结


def _experts() -> list[dict[str, str]]:
    return [{"name": "林岚"}, {"name": "周衡"}]


@pytest.mark.parametrize("topic", TOPICS)
def test_mock_consensus_fits_the_card_limit(topic):
    """共识条目要装进卡片上限（``CONTENT_MAX_LEN``），不能靠后端硬截断。"""
    from backend.flows.consensus import CONTENT_MAX_LEN

    items = json.loads(mock_text("consensus", {"topic": topic, "experts": _experts()}))

    assert items
    assert all(len(item["content"]) <= CONTENT_MAX_LEN for item in items), items


def test_mock_summary_does_not_stack_the_same_speaker_twice():
    """只有一位专家留下可引原句时，总结不能写成「某某说的……和某某说的……」."""
    payload = {
        "topic": "该不该禁止中小学生带手机进校园？",
        "experts": _experts(),
        "insights": [],
        "transcript": [
            {"speaker": "沈知远", "content": "先请两位各给一个判断。"},
            {"speaker": "周衡", "content": "不该禁止，一禁了之数字素养这门课就没了。"},
        ],
    }

    summary = mock_text("summary", payload)

    assert "周衡说的「" not in summary or summary.count("周衡说的「") == 1
    assert "死结" in summary
