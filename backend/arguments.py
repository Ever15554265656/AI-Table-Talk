"""议题论证引擎：把 Mock 发言从「扁平观点池」升级为「议题参数化的分层论证链」。

三条硬约束（本轮验收口径）
--------------------------
1. **不脱离议题**：每一句都由 ``{keyword}`` / ``{topic}`` 填空。素材层只保留「议题域机制」
   与「场景锚点（场景 / 时间线 / 结构性数字）」，不再内置任何具体议题的案例 ——
   旧版把「手机、67% 教师调研、AI 威胁生存、高盛 3 亿岗位」写死在素材里，换个议题就串题。
2. **反驳不复读**：交锋只复述对方「关键判断词」（``key_claim``，≤10 字），随即转折给自己的
   机制与例证，禁止整句照搬。
3. **句式不重复**：论断 / 理由 / 例证 / 反驳 / 让步五类骨架各 ≥5 条，由「句式前缀 × 落点后缀」
   组合出 20+ 种句式，并在会话级去重（``used_skeletons``）。

分层：陈述 → 交锋 → 深化 → 修正（``phase_for``）；收尾只做有条件让步，支持方全程不倒戈。
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Mapping

from backend import topics

#: 单条发言上限（与 ``flows.speaking.SPEECH_MAX_LEN`` 对齐，留 2 字余量给标点）
SPEECH_LIMIT: int = 118

#: 单条发言下限：低于这个长度读起来像敷衍
SPEECH_FLOOR: int = 40

#: 收尾轮起点比例：讨论走过 70% 后进入深化层（开始收束）
CLOSING_RATIO: float = 0.7

#: 修正层占比：只有最后约 15% 轮次允许做有条件让步
CORRECTION_RATIO: float = 0.15

#: 反驳里复述对方「关键判断词」的长度上限（超过就变成复读了）
KEY_CLAIM_LIMIT: int = 10

#: 议题问法里能直接切出「被问的事」的疑问标记
FUTURE_MARKERS: tuple[str, ...] = (
    "会不会",
    "还能不能",
    "能不能",
    "可不可以",
    "能否",
    "是不是",
)
FACT_MARKERS: tuple[str, ...] = ("是否",)
#: 「该不该」类问法的肯定 / 否定答法（值：支持答法, 反对答法）
SHOULD_MARKERS: Mapping[str, tuple[str, str]] = {
    "应不应该": ("该", "不该"),
    "该不该": ("该", "不该"),
    "要不要": ("要", "不要"),
    "值不值得": ("值得", "不值得"),
}
HOW_MARKERS: tuple[str, ...] = ("如何", "怎么", "怎样")

#: 「被问的事」前面常见的虚词：切出来之后要剥掉，否则会有「到底威胁到…」这种半截话
TAIL_NOISE: tuple[str, ...] = (
    "到底",
    "究竟",
    "真的",
    "确实",
    "其实",
    "一定",
    "依然",
    "仍然",
    "还是",
    "也",
    "就",
    "都",
)

#: 「被问的事」最大长度（再长就撑爆 120 字上限）
QUESTION_TAIL_LIMIT: int = 14

#: 发言分层（``phase_for`` 的输出，也是测试与排障用的公开词汇）
PHASE_STATEMENT = "陈述"
PHASE_CLASH = "交锋"
PHASE_DEEPEN = "深化"
PHASE_CORRECTION = "修正"

PHASES: tuple[str, str, str, str] = (
    PHASE_STATEMENT,
    PHASE_CLASH,
    PHASE_DEEPEN,
    PHASE_CORRECTION,
)

#: 议题开头的问法前缀：当关键词念不出宾语时要先剥掉
QUESTION_LEADS: tuple[str, ...] = (
    "应不应该",
    "该不该",
    "要不要",
    "是不是",
    "会不会",
    "能不能",
    "有无必要",
    "是否",
    "如何",
    "怎么",
    "怎样",
)

#: 剥完前缀后仍要剥的介词 / 虚词，让「关于X」读得顺
_PROBE_NOISE: tuple[str, ...] = ("到底", "究竟", "真的", "应该", "对", "把", "让", "继续")

# ============================================================ 数据结构


@dataclass(frozen=True)
class Reason:
    """一条机制理由：``mechanism`` 是机制维度，``evidence`` 是可核对的场景锚点。

    ``evidence`` 只写「场景 / 时间线 / 结构性数字」，不写任何具体议题的统计数字：
    素材一旦绑定某个议题，换题就会串题（旧版 67%、3 亿岗位就是这么来的）。
    """

    mechanism: str
    text: str
    evidence: str


@dataclass(frozen=True)
class SideArguments:
    """一个议题域里**一个立场阵营**的论证素材。"""

    #: 回答问法后的展开（不含判断词，避免与 ``judgement_phrase`` 重复）
    consequence: str
    reasons: tuple[Reason, ...]
    #: 本阵营最容易被对方攻击的点（按席位轮换，避免全场同一句）
    weaknesses: tuple[str, ...]
    #: 收尾轮的有条件让步句
    concession: str


@dataclass(frozen=True)
class DomainArguments:
    """一个议题域的论证素材总表（与 ``topics`` 的域 key 一一对应）。"""

    key: str
    sides: Mapping[str, SideArguments]
    #: 该域可念进句子的具体场景（课堂 / 考核周期 / 早高峰…）
    scene: str
    #: 给主持人的收尾建议（落在这个域的可执行动作上）
    advice: str
    #: 这个域真正的死结（收尾总结的「没谈拢」段落用它）
    deadlock: str
    #: 两边都不否认的那条前提（共识条目用，长度压在 22 字内）
    common_ground: str
    #: 识别不出对手席位时用的通用攻击点
    generic_weaknesses: tuple[str, ...]


@dataclass(frozen=True)
class ArgumentPack:
    """一个席位的完整论证包（验收要求「落到具体层面」的四项都在里面）。"""

    #: 立场标签：支持 / 反对 / 中立
    stance: str
    #: 核心论断：直接回答议题问法（如「不该禁…」）
    judgement: str
    #: 论断之后的展开（为什么这样判断）
    consequence: str
    #: 2~3 条支撑理由（第一条固定为席位自带的论据内核）
    reasons: tuple[str, ...]
    #: 每条理由对应的机制维度
    mechanisms: tuple[str, ...]
    #: 场景锚点池（场景 / 时间线 / 结构性数字）
    evidence: tuple[str, ...]
    #: 该域可念进句子的具体场景
    scene: str
    #: 议题关键词（每条发言必须出现）
    keyword: str
    #: 预埋弱点：本立场最可能被对方阵营攻击的点
    weakness: str
    #: 收尾轮的让步句
    concession: str
    #: 该域的收尾建议（主持人总结用）
    advice: str
    #: 同阵营内的席位序号：用它错开深化层轮到的机制维度
    offset: int = 0

    @property
    def thesis(self) -> str:
        """核心论断的完整表述（判断 + 展开；没有展开句时就只有判断）。"""
        if not self.consequence:
            return self.judgement
        return f"{self.judgement}——{self.consequence}"


# ============================================================ 问法识别


def _strip_tail_noise(tail: str) -> str:
    """剥掉「到底 / 真的 / 确实」这类虚词，让「会 + 尾句」读得通顺。"""
    text = tail.strip()
    changed = True
    while changed and text:
        changed = False
        for noise in TAIL_NOISE:
            if text.startswith(noise) and len(text) > len(noise):
                text = text[len(noise) :].lstrip()
                changed = True
                break
    return text


def question_form(topic: str) -> tuple[str, str, str]:
    """识别议题的问法，返回 ``(family, marker, tail)``。

    ``family`` 取 ``future``（会不会）/ ``fact``（是否）/ ``should``（该不该）/
    ``how``（如何）/ ``statement``（不是问句）；``tail`` 是「被问的那件事」。
    """
    text = " ".join(str(topic or "").split()).strip("「」“”'\" ")
    candidates: list[tuple[int, str, str]] = []
    for markers, family in (
        (FUTURE_MARKERS, "future"),
        (FACT_MARKERS, "fact"),
        (tuple(SHOULD_MARKERS), "should"),
        (HOW_MARKERS, "how"),
    ):
        for marker in markers:
            index = text.find(marker)
            if index >= 0:
                candidates.append((index, marker, family))
    if not candidates:
        return ("statement", "", text)
    # 位置最靠前的标记决定问法；同位置时长标记优先（「应不应该」优先于「要不要」）
    candidates.sort(key=lambda item: (item[0], -len(item[1])))
    index, marker, family = candidates[0]
    tail = _strip_tail_noise(text[index + len(marker) :])
    tail = tail.strip("，、；：,.;:!？?。 ")
    return (family, marker, tail[:QUESTION_TAIL_LIMIT].rstrip("，、；：,.;: "))


def judgement_phrase(topic: str, stance: str) -> str:
    """把「立场 + 议题问法」压成一句**正面回答**（会 / 不会、该 / 不该、有利 / 不利）。"""
    family, marker, tail = question_form(topic)
    label = topics.stance_label(stance)
    if family == "future":
        if label == "中立":
            return f"会{tail}，只是拐点比支持方想的晚" if tail else "会发生，只是拐点比支持方想的晚"
        verb = {"支持": "会", "反对": "不会"}[label]
        if tail:
            return f"{verb}{tail}"
        return {"支持": "会成立", "反对": "不会成立"}[label]
    if family == "fact":
        if label == "中立":
            return f"确实{tail}，只是程度被高估" if tail else "确实如此，只是程度被高估"
        verb = {"支持": "确实", "反对": "谈不上"}[label]
        if tail:
            return f"{verb}{tail}"
        return {"支持": "确实如此", "反对": "谈不上"}[label]
    if family == "should":
        yes, no = SHOULD_MARKERS[marker]
        if not tail:
            return {"支持": "该做", "反对": "不该做", "中立": "该做，但得带例外条款"}[label]
        if label == "支持":
            return f"{yes}{tail}"
        if label == "反对":
            return f"{no}{tail}"
        return f"{yes}{tail}，但必须带例外条款"
    if family == "how":
        return {
            "支持": "先做起来，边做边修",
            "反对": "先别动，把代价算清",
            "中立": "先拆成可验收的指标",
        }[label]
    return {"支持": "我赞成这个方向", "反对": "我反对这个方向", "中立": "方向上该动，条件先摆清"}[label]


def stance_core(stance: str) -> str:
    """从立场文案里剥出「论据内核」（去掉「支持『议题』：」这类前缀与议题引用）。"""
    text = " ".join(str(stance or "").split())
    if not text:
        return ""
    for sep in ("：", ":"):
        _, found, tail = text.partition(sep)
        if found and tail.strip():
            text = tail.strip()
            break
    if text.startswith("「") and "」" in text:
        text = text.split("」", 1)[1].lstrip("取决于—-— ")
    return text[:32].rstrip("，、；：,.;:?。")


def _probe_keyword(topic: str) -> str:
    """从议题里剥出可以直接念的宾语短语（「该不该对一次性塑料加税？」→「一次性塑料加税」）。"""
    text = " ".join(str(topic or "").split()).strip("「」“”'\" ")
    for cut in ("？", "?", "。", "！", "!"):
        text = text.split(cut)[0].strip()
    changed = True
    while changed and text:
        changed = False
        for lead in QUESTION_LEADS:
            if text.startswith(lead) and len(text) > len(lead):
                text = text[len(lead) :].lstrip()
                changed = True
                break
    for noise in _PROBE_NOISE:
        while text.startswith(noise) and len(text) > len(noise) + 3:
            text = text[len(noise) :].lstrip()
    return text.strip("，、；：,.;:?! ")[:12].rstrip("，、；：,.;:?! ")


def primary_keyword(topic: str) -> str:
    """议题里最适合念进句子的关键词（最长且最靠前，仍是议题原文里的词）。"""
    keywords = topics.match_keywords(topic)
    if keywords:
        ranked = sorted(keywords, key=lambda word: (-len(word), keywords.index(word)))
        word = ranked[0]
        if not word.startswith(QUESTION_LEADS) and word != "这个议题":
            return word
    probe = _probe_keyword(topic)
    return probe or topics.topic_brief(topic, 8)


def advice_for(topic: str) -> str:
    """该议题域的收尾建议（主持人总结用，落在一个可执行动作上）。"""
    return arguments_for_topic(topic).advice


def deadlock_for(topic: str) -> str:
    """该议题域真正的死结（收尾总结的「没谈拢」段落用它）。"""
    return arguments_for_topic(topic).deadlock


def common_ground_for(topic: str) -> str:
    """该议题域双方都不否认的前提（收尾共识段用）。"""
    return arguments_for_topic(topic).common_ground


# ============================================================ 对方判断词 / 引用

#: 强判断词（按强度排序）：反驳复述时优先取这些词所在的短句
CLAIM_VERBS: tuple[str, ...] = (
    "谈不上",
    "不该",
    "该",
    "不会",
    "会",
    "不值得",
    "值得",
    "不要",
    "要",
    "确实",
    "不是",
    "就是",
    "没有",
    "没",
)

#: 这些开头的分句是「接话垫片」，不当作对方的判断词
CLAIM_STOP: tuple[str, ...] = (
    "我不同意",
    "我不接",
    "我觉得",
    "我认为",
    "接着",
    "关于",
    "直说结论",
    "先把",
    "等一下",
    "换个角度",
    "补一句",
    "顺着",
    "听下来",
    "对手在",
    "不绕圈子",
    "说穿了",
    "原因很实在",
    "从执行",
    "从分配",
    "从数据",
    "从一线",
)


def key_claim(content: str, limit: int = KEY_CLAIM_LIMIT) -> str:
    """从对方发言里取出「关键判断词」（≤10 字），供反驳复述。

    只在发言**开头几句**里找判断动词（「谈不上有利」「确实有利」「不该…」），
    找不到才退回最短分句 —— 这样拿到的是判断，而不是后半句的举例残片。
    """
    text = " ".join(str(content or "").split()).strip()
    if not text:
        return ""
    # 对方自己就在反驳别人（点名 + 转折）：这句里没有他自己的判断可复述，
    # 再「复述」只会把那句接话垫片当成判断词（「林岚这一条我不同意」）
    if any(
        marker in text
        for marker in (
            "我不这么看",
            "还有另一半",
            "我补的是反面",
            "我只认一半",
            "这一条我不同意",
            "把话说到",
            "的关键判断是",
        )
    ):
        return ""
    # 对方本身在转述一句判断时（「X 的关键判断是「…」」），引号里就是这场争论的判断，
    # 复述它才算接住同一个争点；但如果引号里只是一句长引用，就退回引号之后的自己的话
    if "「" in text and "」" in text:
        inner = text.split("「", 1)[1].split("」", 1)[0]
        after = text.split("」", 1)[1].lstrip("，、；：,;:—- ")
        text = inner if 2 <= len(inner) <= 24 else (after or text.split("「", 1)[0])
    clauses = [
        clause.strip(" 「」“”‘’'\"")
        for clause in re.split(r"[，。；！？、,;!?：:—]+", text)
    ]
    clauses = [clause for clause in clauses if len(clause) >= 2]
    clauses = [
        clause
        for clause in clauses
        if not any(clause.startswith(stop) for stop in CLAIM_STOP)
    ]
    if not clauses:
        return ""
    # 判断句一般在最前面三句里：先按「强判断词 → 出现位置」取
    for clause in clauses[:3]:
        for verb in CLAIM_VERBS:
            position = clause.find(verb)
            if position < 0:
                continue
            phrase = clause[position : position + limit]
            phrase = phrase.rstrip("的和与在把对是了也都就，、")
            if len(phrase) >= 2:
                return phrase
    # 没有判断动词：用一个能装进上限的短句，绝不整句照搬
    for clause in clauses:
        if len(clause) <= limit:
            return clause
    return clauses[0][:limit].rstrip("，、；：,.;:—- ")


def quote_clause(content: str, limit: int = 14) -> str:
    """从发言里切一段可引用的短片段（总结段用；交锋层已改为 ``key_claim``）。"""
    text = str(content or "")
    if not text or "「" in text or "」" in text:
        return ""
    clauses = [
        clause.strip(" 「」“”‘’'\"")
        for clause in re.split(r"[，。；！？、,;!?：:—]+", text)
    ]
    clauses = [clause for clause in clauses if len(clause) >= 3]
    clauses = [
        clause
        for clause in clauses
        if not any(clause.startswith(stop) for stop in CLAIM_STOP)
    ]
    fitting = [clause for clause in clauses if len(clause) <= limit]
    if not fitting:
        return ""
    return max(fitting, key=len)


# ============================================================ 骨架目录

#: 句式前缀（论断）：全部带议题关键词，换题即换句
STATEMENT_HEADS: tuple[str, ...] = (
    "关于{keyword}，我的判断很明确：",
    "直说结论，{keyword}上：",
    "先把{keyword}的答案摆出来：",
    "不绕圈子，{keyword}这件事：",
    "{keyword}上我给个明确判断：",
    "先回答{keyword}：",
)

#: 论断的落点：{angle} 是席位视角词，保证同阵营两人判断句不会一字不差
STATEMENT_TAILS: tuple[str, ...] = (
    "，{angle}。",
    "，{angle}，这话我负责。",
    "，{angle}，先别急着贴标签。",
    "——{angle}，账摆在这儿。",
)

#: 席位视角词（按同阵营序号取）：同一个人每轮换句式，不同席位换视角
STATEMENT_ANGLES: tuple[str, ...] = (
    "从执行这一端看",
    "从分配这一端看",
    "从数据这一端看",
    "从一线这一端看",
)

#: 理由前缀：机制句一定要挂在议题上
REASON_HEADS: tuple[str, ...] = (
    "{keyword}的机制在这儿：{reason}",
    "{keyword}这一层，卡的是：{reason}",
    "原因很实在，{keyword}卡在：{reason}",
    "说穿了就一句：{reason}，{keyword}也一样",
    "往深一层看{keyword}：{reason}",
    "把{keyword}的账算到底：{reason}",
)

#: 理由落点：接场景锚点
REASON_TAILS: tuple[str, ...] = (
    "；{evidence}。",
    "，{evidence}。",
    "——{evidence}。",
    "；具体看，{evidence}。",
)

#: 例证前缀：场景 / 时间线 / 结构性数字三选一（关键词只出现在一部分句式里，避免一句念三遍）
EVIDENCE_HEADS: tuple[str, ...] = (
    "放到{scene}里看，{evidence}",
    "一个可核对的场景是：{evidence}",
    "具体到{keyword}，{evidence}",
    "往下追一层：{evidence}",
    "换个场景问{keyword}，{evidence}",
    "账在这儿：{evidence}",
)

EVIDENCE_TAILS: tuple[str, ...] = (
    "。",
    "，这一层绕不开。",
    "，差别就在这一步。",
    "，这不是口号能替代的。",
)

#: 反驳前缀：复述对方「关键判断词」后立刻转折（每条都必须点名）
CLASH_HEADS: tuple[str, ...] = (
    "{name}的关键判断是「{key}」，{keyword}上我不这么看：",
    "{name}把话说到「{key}」，可{keyword}上还有另一半：",
    "{name}定的是「{key}」，{keyword}上我补的是反面：",
    "听下来{name}就一句「{key}」，{keyword}这一关却躲不过：",
    "{name}那句「{key}」在{keyword}上我只认一半：",
    "先接住{name}的「{key}」，{keyword}这笔账还得算完：",
)

CLASH_TAILS: tuple[str, ...] = (
    "{attack}——{evidence}。",
    "{attack}；{evidence}是另一条线。",
    "{attack}；{evidence}放在这儿，结论就反过来了。",
    "{attack}，具体看{evidence}。",
)

#: 让步前缀：修正层只做有条件让步
CONCESSION_HEADS: tuple[str, ...] = (
    "{keyword}这块我认一半：",
    "先把话说软一点，{keyword}这块：",
    "对手在{keyword}上确实说到位：",
    "我先把{keyword}的口径收一收：",
    "{keyword}这件事，我先服一半：",
    "不装硬，{keyword}上：",
)

CONCESSION_TAILS: tuple[str, ...] = (
    "{concession}；但{keep}这一条我不改——{reason}，{evidence}。",
    "{concession}。可{keep}这个判断我保留：{reason}，{evidence}。",
    "{concession}，这话我认；不过{keep}，这一点我不退，因为{reason}，{evidence}。",
    "{concession}；{keep}依然成立：{reason}，{evidence}。",
)

#: 深化前缀（不需要点名时用）
DEEPEN_HEADS: tuple[str, ...] = (
    "把{keyword}放回具体场景看：",
    "{keyword}还有一层没被提到：",
    "再往下追一步，{keyword}还有一层：",
    "换个角度问{keyword}：",
    "顺着这个判断往深一层，{keyword}上：",
    "补一个被跳过的前提，{keyword}这块：",
)

#: 深化前缀（点着某人往下推）
DEEPEN_NAMED_HEADS: tuple[str, ...] = (
    "{name}在{keyword}上的那一点，我往下推一层：",
    "把{name}对{keyword}的判断再追一步：",
    "{name}说到点子上了，{keyword}我再补一层：",
    "沿着{name}这条线走到底，{keyword}上：",
    "{name}只说了半句，{keyword}的后半句是：",
)

DEEPEN_TAILS: tuple[str, ...] = (
    "{reason}，{evidence}。",
    "{reason}——{evidence}。",
    "{reason}；{evidence}，这一步绕不过去。",
    "{evidence}，而{reason}。",
)

#: 接话前缀（反驳 / 补充但拿不到可复述的判断词时用）
ADDRESS_OPPOSE: tuple[str, ...] = (
    "{name}在{keyword}上的结论我不接：",
    "{name}这一条我不同意，{keyword}上：",
    "跟{name}在{keyword}上的判断正相反：",
    "{name}把{keyword}的方向说反了：",
    "我不认同{name}在{keyword}上的结论：",
)

ADDRESS_ALLY: tuple[str, ...] = (
    "接着{name}的话说：",
    "按{name}的思路再往前一步：",
    "{name}这一点我补一句：",
    "顺着{name}往下说：",
    "我先接住{name}：",
)

#: 五类必备骨架（每类 ≥5 条前缀 × ≥4 个落点 → 20+ 种句式，够一场 20 条发言不重样）
SKELETON_KINDS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "statement": (STATEMENT_HEADS, STATEMENT_TAILS),
    "reason": (REASON_HEADS, REASON_TAILS),
    "evidence": (EVIDENCE_HEADS, EVIDENCE_TAILS),
    "clash": (CLASH_HEADS, CLASH_TAILS),
    "concession": (CONCESSION_HEADS, CONCESSION_TAILS),
}


def _skeleton(
    heads: tuple[str, ...],
    tails: tuple[str, ...],
    *,
    kind: str,
    step: int,
    offset: int,
    used: set[str] | None,
) -> tuple[str, str]:
    """按「前缀 × 落点」取一组骨架，并在会话级去重（``used`` 为 None 表示不去重）。"""
    total = len(heads) * len(tails)
    if total <= 0:
        return ("", "")
    start = (int(step) * 5 + int(offset) * 3) % total
    for shift in range(total):
        index = (start + shift) % total
        head_index, tail_index = divmod(index, len(tails))
        skeleton_id = f"{kind}:{head_index}:{tail_index}"
        if used is None or skeleton_id not in used:
            if used is not None:
                used.add(skeleton_id)
            return heads[head_index], tails[tail_index]
    # 全场都用过了（超长场次）：按序号轮转，至少不会连着两轮同一句
    head_index, tail_index = divmod(start % total, len(tails))
    return heads[head_index], tails[tail_index]


#: 骨架槽位占位符（只认 ``{name}`` 这种简单形式）
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _fill(template: str, **slots: object) -> str:
    """填骨架槽位：缺省槽位一律填空串，绝不因缺少某个槽位打断整场讨论。"""
    values = {key: ("" if value is None else str(value)) for key, value in slots.items()}
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), ""), template)


# ============================================================ 域 1：技术与互联网

TECH_ARGUMENTS: DomainArguments = DomainArguments(
    key="tech",
    scene="一条审批链",
    sides={
        "支持": SideArguments(
            consequence="先被改写的是判断权和试错空间，产出会先动起来",
            reasons=(
                Reason(
                    mechanism="技术边界",
                    text="工具先吃掉重复环节，剩下的判断反而更值钱",
                    evidence="头三个月基本都在重训工具，第四个月才开始显形",
                ),
                Reason(
                    mechanism="部署成本",
                    text="算力和人力的钱先卡住小机构，落地比演示慢得多",
                    evidence="一套流程跑通要过三道审批",
                ),
                Reason(
                    mechanism="能力台阶",
                    text="能力是一级一级抬的，每抬一级都要重做一轮验证",
                    evidence="每一级台阶之间隔着一次全量回归",
                ),
            ),
            weaknesses=(
                "没回答出事之后谁签字",
                "把上线速度算得太乐观",
            ),
            concession="短期冲击我认，比我预想的更猛",
        ),
        "反对": SideArguments(
            consequence="被替代的是重复劳动，但重组的成本落在一线",
            reasons=(
                Reason(
                    mechanism="责任归属",
                    text="追责链没写清，出事时没人担得起",
                    evidence="审计链路里少一环，事故就查不下去",
                ),
                Reason(
                    mechanism="替代边界",
                    text="被拆走的是任务，重组之后交接反而更费人",
                    evidence="一个岗位被切成三段，交接时间比原来更长",
                ),
                Reason(
                    mechanism="激励错位",
                    text="买它是为了省成本，不是为了把人换掉",
                    evidence="预算表上先砍的是外包和加班费",
                ),
            ),
            weaknesses=(
                "只谈工具没动机，回避了能力落到谁手里",
                "把风险全推给责任归属，没算执行成本",
            ),
            concession="对方说的失控场景我承认存在",
        ),
        "中立": SideArguments(
            consequence="风险不取决于能力上限，取决于开关在谁手里",
            reasons=(
                Reason(
                    mechanism="监管周期",
                    text="规矩总比技术慢一拍，滞后期风险最高",
                    evidence="标准落地一般要跨两个预算年度",
                ),
                Reason(
                    mechanism="开关归属",
                    text="同一个能力换一个场景用，结论就反过来",
                    evidence="决定权在谁手里，比参数高低更关键",
                ),
                Reason(
                    mechanism="历史先例",
                    text="每代通用技术都先乱一阵，再补规矩",
                    evidence="上一轮技术普及，也是先把事故的学费交完",
                ),
            ),
            weaknesses=(
                "用看条件躲开了该往哪边投票",
                "把责任推给监管，没说自己主张什么",
            ),
            concession="看条件这句在直播里最不讨好，我认",
        ),
    },
    advice="把责任归属和验收口径先写进合同",
    deadlock="该先卡能力上限，还是先卡责任归属",
    common_ground="能力只在自己可控的环节兑现",
    generic_weaknesses=(
        "没回答代价落在谁头上",
        "把时间线算得太乐观",
        "回避了执行成本",
    ),
)


# ============================================================ 域 2：职场与组织

WORKPLACE_ARGUMENTS: DomainArguments = DomainArguments(
    key="workplace",
    scene="一个考核周期",
    sides={
        "支持": SideArguments(
            consequence="产出要是能量化，工时的松紧才有讨论价值",
            reasons=(
                Reason(
                    mechanism="产出可衡量",
                    text="产出能量化，工时才有意义",
                    evidence="一个考核周期就能看出差别",
                ),
                Reason(
                    mechanism="反馈周期",
                    text="短周期反馈更容易纠偏，问题不会攒到年底",
                    evidence="每个季度复盘一次，改动才赶得上",
                ),
                Reason(
                    mechanism="留任意愿",
                    text="节奏松一点，离职意愿通常先降",
                    evidence="试用期前三个月最能看出留任意愿",
                ),
            ),
            weaknesses=(
                "没回答交付节奏怎么保住",
                "把员工恢复感当成了产出",
            ),
            concession="成本这一块我承认被低估过",
        ),
        "反对": SideArguments(
            consequence="交付节奏不会跟着政策走，压力最后落在一线",
            reasons=(
                Reason(
                    mechanism="交付节奏",
                    text="节奏一压，客户响应先崩，成本最后落在一线",
                    evidence="一个项目拖两周，后面全串期",
                ),
                Reason(
                    mechanism="成本转嫁",
                    text="省下来的时间会被下一个任务填满",
                    evidence="同一批人接的活一条没少",
                ),
                Reason(
                    mechanism="议价能力",
                    text="一线没有议价空间，排班一改就是人手缺口",
                    evidence="最早的排班表，最先被改的是最基层",
                ),
            ),
            weaknesses=(
                "只讲成本不讲产出怎么衡量",
                "把管理问题说成了政策问题",
            ),
            concession="员工恢复感确实是真的，这一点我不否认",
        ),
        "中立": SideArguments(
            consequence="考核口径不改，压缩工时只是把同样的活压进更少的天数",
            reasons=(
                Reason(
                    mechanism="考核口径",
                    text="同一份产出，两种口径能算出两个结论",
                    evidence="口径不写清楚，数据怎么好看怎么来",
                ),
                Reason(
                    mechanism="试点周期",
                    text="一个周期的数据不够，至少跑满两个",
                    evidence="第一个周期通常还在磨合",
                ),
                Reason(
                    mechanism="分配机制",
                    text="时间省下来了，归属却没写清楚",
                    evidence="多出来的时间是归个人还是归团队，没人说",
                ),
            ),
            weaknesses=(
                "中立到最后没有立场，观众记不住",
                "用看数据挡住了所有判断",
            ),
            concession="要说立场，我偏向先小范围试",
        ),
    },
    advice="先在一个小单元跑满两个考核周期",
    deadlock="省下来的时间归组织还是归个人",
    common_ground="产出怎么算，比工时怎么排更关键",
    generic_weaknesses=(
        "没回答代价落在谁头上",
        "把时间线算得太乐观",
        "回避了执行成本",
    ),
)


# ============================================================ 域 3：教育与学习

EDUCATION_ARGUMENTS: DomainArguments = DomainArguments(
    key="education",
    scene="一个班的课堂",
    sides={
        "支持": SideArguments(
            consequence="先把学习效率这一层改写：反馈变快，练得更准",
            reasons=(
                Reason(
                    mechanism="学习效率",
                    text="反馈越快，练得越准，错题不用等下一次考试才被发现",
                    evidence="一个单元的知识点，当天就能回炉一遍",
                ),
                Reason(
                    mechanism="因材施教",
                    text="同一个班进度差三档，分层练习才谈得上因材施教",
                    evidence="一个班四十来号人，老师没法逐个盯",
                ),
                Reason(
                    mechanism="时间分配",
                    text="省下来的是重复讲解的时间，不是思考的时间",
                    evidence="一节课的讲解能压到二十来分钟",
                ),
            ),
            weaknesses=(
                "没回答学生会不会因此不动脑",
                "把效率等同于成绩",
            ),
            concession="依赖风险这一条我承认存在",
        ),
        "反对": SideArguments(
            consequence="替学生把思考做完，练出来的是依赖不是能力",
            reasons=(
                Reason(
                    mechanism="思维惰性",
                    text="思考被工具代劳，练出来的是依赖",
                    evidence="作业一给现成答案，第二天提问就没人举手",
                ),
                Reason(
                    mechanism="评价错位",
                    text="评价还是看分数，工具再好也只是加速旧路径",
                    evidence="期末还是那张卷子，方法变了考核没变",
                ),
                Reason(
                    mechanism="资源差距",
                    text="先能用上的总是资源好的那批，差距会被放大",
                    evidence="两个学校之间，设备差着一代",
                ),
            ),
            weaknesses=(
                "只讲风险，没给可用的替代方案",
                "把学生想得太被动",
            ),
            concession="效率确实提上去了，这一点我不否认",
        ),
        "中立": SideArguments(
            consequence="关键不是用不用，而是哪一段能交出去",
            reasons=(
                Reason(
                    mechanism="使用边界",
                    text="同一个工具，查资料和抄答案只差一步",
                    evidence="边界不写清楚，执行就靠自觉",
                ),
                Reason(
                    mechanism="教师负担",
                    text="老师要先学会用，培训成本最后压在备课时间上",
                    evidence="一次教研活动撑不起整套流程",
                ),
                Reason(
                    mechanism="效果归因",
                    text="成绩涨了也说不清是哪一环起的作用",
                    evidence="一个学期里变量太多，单看分数归不了因",
                ),
            ),
            weaknesses=(
                "用条件躲开了该往哪边投票",
                "把判断推给了老师",
            ),
            concession="边界没写清这一点，我认",
        ),
    },
    advice="先把评价口径和例外条款写清楚",
    deadlock="该先改评价，还是先改工具",
    common_ground="学会思考这件事，工具替不了",
    generic_weaknesses=(
        "没回答代价落在谁头上",
        "把时间线算得太乐观",
        "回避了执行成本",
    ),
)


# ============================================================ 域 4：城市与公共政策

CITY_ARGUMENTS: DomainArguments = DomainArguments(
    key="city",
    scene="早高峰",
    sides={
        "支持": SideArguments(
            consequence="先动需求侧，短期能压住峰值",
            reasons=(
                Reason(
                    mechanism="需求管理",
                    text="把时间成本显性化，才有人真的改路线",
                    evidence="高峰挪开半小时，拥挤就松一档",
                ),
                Reason(
                    mechanism="供给节奏",
                    text="补供给是跨年度的事，短期只能先管需求",
                    evidence="一条主干道从立项到通车要好几年",
                ),
                Reason(
                    mechanism="可执行性",
                    text="规则能不能落地，看现场有没有人查",
                    evidence="一条规则写三句，最好只留一句能罚的",
                ),
            ),
            weaknesses=(
                "没算被挤出去的那批人",
                "把短期效果当成了长期解",
            ),
            concession="被挤到外围的成本，我认这一笔",
        ),
        "反对": SideArguments(
            consequence="代价会先落在没有替代方式的人身上",
            reasons=(
                Reason(
                    mechanism="代价分配",
                    text="对成本最敏感的人，往往也是没得选的人",
                    evidence="换乘一次，单程多花四十分钟",
                ),
                Reason(
                    mechanism="替代方案",
                    text="没有替代就是硬扛，规则再合理也会被绕",
                    evidence="夜班下班的人，找不到第二个选择",
                ),
                Reason(
                    mechanism="反弹效应",
                    text="压下去的需求，过一阵会从别的口子回来",
                    evidence="三个月后，流量又爬回原来的水平",
                ),
            ),
            weaknesses=(
                "只反对，没给出替代的治理工具",
                "拿个体感受替代了总量数据",
            ),
            concession="短期确实压住了峰值，我不否认",
        ),
        "中立": SideArguments(
            consequence="供给、住房、通勤是一张表，单拎一条都推不动",
            reasons=(
                Reason(
                    mechanism="政策配套",
                    text="三个部门各管一段，最后压力全落在居民身上",
                    evidence="三张表不打通，只能各管一段",
                ),
                Reason(
                    mechanism="试点验证",
                    text="一条街的试点数据，比全城推演可靠",
                    evidence="先跑一个片区，再谈全域",
                ),
                Reason(
                    mechanism="验收指标",
                    text="只看总量不看分群，结论会反过来",
                    evidence="同一组数据，分群看结论就变了",
                ),
            ),
            weaknesses=(
                "用看条件躲开了该往哪边投票",
                "把判断推给了下一届",
            ),
            concession="配套说了十年，还是没给时间表",
        ),
    },
    advice="把供给、通勤、成本三张表先打通一张",
    deadlock="该先扩供给，还是先压住需求",
    common_ground="短期能压住的，长期都要还回来",
    generic_weaknesses=(
        "没回答代价落在谁头上",
        "把时间线算得太乐观",
        "回避了执行成本",
    ),
)


# ============================================================ 域 5：青少年与注意力

YOUTH_ARGUMENTS: DomainArguments = DomainArguments(
    key="youth",
    scene="一个学期",
    sides={
        "支持": SideArguments(
            consequence="先给边界再讲道理，注意力才守得住",
            reasons=(
                Reason(
                    mechanism="注意力",
                    text="注意力是有限资源，被切碎就拼不回来",
                    evidence="一节课被打断两次，后半节基本白上",
                ),
                Reason(
                    mechanism="作息",
                    text="作息一乱，第二天全废",
                    evidence="晚睡一小时，第二天效率掉一半",
                ),
                Reason(
                    mechanism="规则成本",
                    text="规则写清楚，争执反而少一半",
                    evidence="最省事的办法，通常是先说清楚界限",
                ),
            ),
            weaknesses=(
                "没回答替代方案是什么",
                "把管理成本推给了执行者",
            ),
            concession="替代方案这块，我确实没给全",
        ),
        "反对": SideArguments(
            consequence="一禁了之，替代方案没人给",
            reasons=(
                Reason(
                    mechanism="替代方案",
                    text="禁掉一个出口，需求会转到更隐蔽的地方",
                    evidence="越禁止越隐蔽，问题从明面转到暗处",
                ),
                Reason(
                    mechanism="家庭责任",
                    text="家庭管理缺位的地方，外部规则补不上",
                    evidence="同一个规则，两个家庭执行出两个结果",
                ),
                Reason(
                    mechanism="能力错位",
                    text="用和戒是两种能力，只教戒等于放弃前一种",
                    evidence="会查资料和只会刷，中间差着一套方法",
                ),
            ),
            weaknesses=(
                "只反对，没给可执行的边界",
                "把家庭问题说成了学校问题",
            ),
            concession="边界确实需要一个说法，这一点我认",
        ),
        "中立": SideArguments(
            consequence="问题和工具本身无关，和管理缺位有关",
            reasons=(
                Reason(
                    mechanism="内容分级",
                    text="内容分级比时长限制更有效",
                    evidence="同样的时长，看什么差别很大",
                ),
                Reason(
                    mechanism="工具与内容",
                    text="工具和内容要分开管，别当成一件事",
                    evidence="同一条规则，工具和内容被捆在一起罚",
                ),
                Reason(
                    mechanism="执行成本",
                    text="执行成本最后压在最忙的那个人身上",
                    evidence="一张表填满两周，第三周就没人再看了",
                ),
            ),
            weaknesses=(
                "用看条件躲开了该往哪边投票",
                "把判断推给了执行的人",
            ),
            concession="执行成本这一条，我确实低估了",
        ),
    },
    advice="把内容分级和管理责任同时落到人",
    deadlock="问题是工具本身，还是管理缺位",
    common_ground="问题不在工具本身，在谁在管",
    generic_weaknesses=(
        "没回答代价落在谁头上",
        "把时间线算得太乐观",
        "回避了执行成本",
    ),
)


# ============================================================ 域 6：通用议题（兜底）

GENERAL_ARGUMENTS: DomainArguments = DomainArguments(
    key="general",
    scene="一次试点",
    sides={
        "支持": SideArguments(
            consequence="收益在前、成本在后，别只盯眼前这一笔",
            reasons=(
                Reason(
                    mechanism="总账",
                    text="头一年看不出差别，第二年才开始显形",
                    evidence="两年的时间线，比一个季度的数字可信",
                ),
                Reason(
                    mechanism="推进节奏",
                    text="方向没错，问题出在节奏和配套",
                    evidence="先把能落地的部分做掉，比等完美方案现实",
                ),
                Reason(
                    mechanism="试点",
                    text="一个点的试点数据，胜过一叠推演",
                    evidence="先跑一个地方，再谈全域",
                ),
            ),
            weaknesses=(
                "没回答代价落在谁头上",
                "把推进速度想得太顺",
            ),
            concession="代价这块我承认算得粗",
        ),
        "反对": SideArguments(
            consequence="难点不在该不该，而在代价最后由谁承担",
            reasons=(
                Reason(
                    mechanism="代价分配",
                    text="账单一摊开，最先被压的是最没议价能力的那批",
                    evidence="同一份成本，落在谁头上结果完全不同",
                ),
                Reason(
                    mechanism="执行细则",
                    text="缺细则，落地一定变形",
                    evidence="口径写得越粗，执行越靠自由裁量",
                ),
                Reason(
                    mechanism="沉默的一方",
                    text="最容易被忽略的人，通常最后才被通知",
                    evidence="政策一落地，最后知道的总是那群人",
                ),
            ),
            weaknesses=(
                "只反对，没给可执行的替代路径",
                "把所有代价都算成了同一笔账",
            ),
            concession="方向没错这一点，我不否认",
        ),
        "中立": SideArguments(
            consequence="平均数会掩盖差异，先看分群再下结论",
            reasons=(
                Reason(
                    mechanism="分群视角",
                    text="同一组数据，分群看结论就反了",
                    evidence="总量好看，落到某一群人身上是另一回事",
                ),
                Reason(
                    mechanism="验收口径",
                    text="指标怎么定、谁来验收，决定政策能不能站住",
                    evidence="同一个目标，两种验收口径两个结果",
                ),
                Reason(
                    mechanism="过渡期",
                    text="过渡期给三个月和给一年，结果不一样",
                    evidence="过渡安排得好不好，比方案本身更影响成败",
                ),
            ),
            weaknesses=(
                "用看数据挡住了所有判断",
                "中立到最后没有立场",
            ),
            concession="要说立场，我偏向先小范围试",
        ),
    },
    advice="先把验收口径和代价分担写进细则",
    deadlock="代价该谁先付、按什么顺序付",
    common_ground="缺的不是立场，是能核对的验收口径",
    generic_weaknesses=(
        "没回答代价落在谁头上",
        "把时间线算得太乐观",
        "回避了执行成本",
    ),
)


#: 域 key → 论证素材（顺序与 ``topics.DOMAINS`` 无关，只按 key 查表）
DOMAIN_ARGUMENTS: dict[str, DomainArguments] = {
    item.key: item
    for item in (
        TECH_ARGUMENTS,
        WORKPLACE_ARGUMENTS,
        EDUCATION_ARGUMENTS,
        CITY_ARGUMENTS,
        YOUTH_ARGUMENTS,
        GENERAL_ARGUMENTS,
    )
}


def arguments_for_topic(topic: str) -> DomainArguments:
    """按议题域取论证素材（识别不出域时落兜底域，与阵容口径一致）。"""
    key = topics.detect_domain(topic).key
    return DOMAIN_ARGUMENTS.get(key) or GENERAL_ARGUMENTS


# ============================================================ 论证包装配


def _same_side_seats(domain: topics.TopicDomain, stance_type: str) -> list[topics.DomainSeat]:
    return [seat for seat in domain.seats if seat.stance_type == stance_type]


def _seat_offset(domain: topics.TopicDomain, seat: topics.DomainSeat) -> int:
    """同一阵营里的序号：用它轮换机制维度与预埋弱点，避免同阵营两人说同一句。"""
    peers = _same_side_seats(domain, seat.stance_type)
    if seat in peers:
        return peers.index(seat)
    return domain.seats.index(seat) if seat in domain.seats else 0


def argument_pack(topic: str, profession: str, stance: str) -> ArgumentPack:
    """为一个席位装配论证包（纯函数：同样的输入永远得到同样的论证包）。"""
    args = arguments_for_topic(topic)
    label = topics.stance_label(stance)
    side = args.sides.get(label) or args.sides["中立"]
    domain = topics.detect_domain(topic)
    seat = topics.seat_for_profession(topic, profession)
    keyword = primary_keyword(topic)

    offset = 0
    core = ""
    if seat is not None:
        offset = _seat_offset(domain, seat)
        core = seat.claims[0] if seat.claims else ""
    else:
        # 没匹配到本域席位（自定义职业）时也要有个稳定序号，
        # 否则同阵营两个自定义职业的席位都会拿到 offset 0，句式必然撞车
        offset = sum(ord(ch) for ch in str(profession or "")) % 3
    if not core:
        core = stance_core(stance) or f"{keyword}这一层得先把账算清楚"

    pivot = offset % len(side.reasons)
    rotated = side.reasons[pivot:] + side.reasons[:pivot]
    # 席位论据内核本身就在理由池里时应跳过，别再拿来当「机制理由」重复一遍
    extra_items = [
        item for item in rotated if item.text != core and core[:6] not in item.text
    ] or list(rotated)
    # 同一阵营的第 2 个席位如果照念阵营展开句，两个人就有一句一字不差：
    # 奇数位改成不念展开句（判断后面直接上自己的论据），偶数位照念
    consequence = side.consequence if offset % 2 == 0 else ""
    # 让步句同理：奇数位改用「自己最容易被攻击的那一点」当让步，和别人错开
    if offset % 2:
        concession = (
            f"{side.weaknesses[(offset + 1) % len(side.weaknesses)]}，这一点我确实答得不够"
        )
    else:
        concession = side.concession
    return ArgumentPack(
        stance=label,
        judgement=judgement_phrase(topic, stance),
        consequence=consequence,
        reasons=(core,) + tuple(item.text for item in extra_items),
        mechanisms=("席位论据",) + tuple(item.mechanism for item in extra_items),
        evidence=tuple(item.evidence for item in extra_items),
        scene=args.scene,
        keyword=keyword,
        weakness=side.weaknesses[offset % len(side.weaknesses)],
        concession=concession,
        advice=args.advice,
        offset=offset,
    )


def identify_seat(topic: str, content: str) -> topics.DomainSeat | None:
    """从一段发言里反查它出自哪个席位（靠论据内核前缀匹配）。"""
    text = str(content or "")
    if not text:
        return None
    domains = (topics.detect_domain(topic), *topics.DOMAINS, topics.FALLBACK_DOMAIN)
    for domain in domains:
        for seat in domain.seats:
            for claim in seat.claims:
                probe = claim[:8]
                if probe and probe in text:
                    return seat
    return None


def same_side(topic: str, content: str, own_stance: str) -> bool:
    """这段发言是否与自己同阵营（同阵营就别喊「我不同意」，改成顺着补充）。"""
    seat = identify_seat(topic, content)
    return bool(seat is not None and seat.stance_type == own_stance)


def attack_line(
    topic: str,
    content: str,
    own_stance: str,
    rng: random.Random,
    *,
    step: int = 0,
    avoid: set[str] | None = None,
) -> str:
    """给对手挑一句「你这里没说完」：优先用对手阵营的预埋弱点。

    ``step`` 参与选句：同一个对手被不同席位连着反驳时，挑的弱点要错开；
    对手刚才自己已经把这句话说出口的、以及本场已经用过的攻击点都不再重复。
    """

    def pick(options: list[str], index: int) -> str:
        chosen = options[index % len(options)]
        if avoid is not None:
            avoid.add(chosen)
        return chosen

    args = arguments_for_topic(topic)
    seat = identify_seat(topic, content)
    if seat is not None and seat.stance_type != own_stance:
        side = args.sides.get(seat.stance_type)
        if side is not None:
            offset = _seat_offset(topics.detect_domain(topic), seat)
            pool = [
                item
                for item in side.weaknesses
                if item and item not in content and (avoid is None or item not in avoid)
            ]
            if pool:
                return pick(pool, offset + int(step or 0))
    pool = [
        item
        for item in args.generic_weaknesses
        if item and item not in content and (avoid is None or item not in avoid)
    ]
    return pick(pool or list(args.generic_weaknesses), int(step or 0))


def pick_opponent_speech(recent: object, reply_to: str, speaker: str) -> tuple[str, str]:
    """从最近发言里找出该回应谁、说了什么（只认有名字有内容的条目）。"""
    entries: list[tuple[str, str]] = []
    for item in recent or ():  # type: ignore[union-attr]
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("speaker") or "").strip()
        content = str(item.get("content") or "").strip()
        if not name or not content or name == speaker:
            continue
        entries.append((name, content))
    if not entries:
        return (str(reply_to or ""), "")
    if reply_to:
        for name, content in reversed(entries):
            if name == reply_to:
                return (name, content)
    return entries[-1]


# ============================================================ 分层发言


def phase_for(turn: int, total_rounds: int, expert_count: int) -> str:
    """按轮次判断当前处于哪一层：陈述 → 交锋 → 深化 → 修正。

    * 头一圈：陈述层，每人先正面表态；
    * 随后：交锋层，必须点名反驳；
    * 走过 70%：深化层，换机制维度收束；
    * 最后约 15%：修正层，允许有条件让步（否则大半场都在认错）。
    """
    turn = max(1, int(turn or 1))
    total = max(1, int(total_rounds or 1))
    experts = max(1, int(expert_count or 1))
    closing_at = max(2, math.ceil(total * CLOSING_RATIO))
    correction_at = max(closing_at, total - max(1, math.ceil(total * CORRECTION_RATIO)))
    statement_end = max(1, min(experts, max(1, total // 2)))
    clash_end = max(statement_end, min(total, statement_end * 2))
    if turn <= statement_end:
        return PHASE_STATEMENT
    if turn >= correction_at:
        return PHASE_CORRECTION
    if turn >= closing_at:
        return PHASE_DEEPEN
    if turn <= clash_end:
        return PHASE_CLASH
    return PHASE_DEEPEN


def _evidence_at(pack: ArgumentPack, index: int) -> str:
    """取第 ``index`` 条场景锚点（池子空了就退回论断展开，绝不让下标越界）。"""
    if not pack.evidence:
        return pack.consequence or pack.reasons[0]
    return pack.evidence[index % len(pack.evidence)]


def _evidence_other(pack: ArgumentPack, first: str, index: int = 0) -> str:
    """再取一条与 ``first`` 不同的锚点，避免同一句话里把同一个例子念两遍。"""
    pool = [item for item in pack.evidence if item and item != first]
    if not pool:
        return first
    return pool[index % len(pool)]


#: 判断句里「附加条款」的起点：修正层只守主句，否则会叠出「但…，但必须带例外条款这一条我不改」
JUDGEMENT_TAIL_MARKERS: tuple[str, ...] = ("，但", "；但", "，只是", "，前提是", "，条件是")


def _plain_judgement(pack: ArgumentPack) -> str:
    """取判断的主句（掐掉「，但…」「，只是…」这类附加条款）。"""
    head = pack.judgement
    for marker in JUDGEMENT_TAIL_MARKERS:
        head = head.split(marker)[0]
    return head.strip("，；。 ")


def _statement_parts(
    pack: ArgumentPack,
    keyword: str,
    rng: random.Random,
    step: int,
    name: str = "",
    oppose: bool = False,
    used: set[str] | None = None,
    skeleton_step: int | None = None,
) -> list[str]:
    """陈述层：正面回答议题 + 一条机制理由 + 一个场景锚点。

    被指针锋相对地回应某人时（``name`` 非空），前面先接一句，但结论不改口。
    同阵营（``oppose=False``）改口说「接着…的话说」，不喊「我不同意」。

    ``step`` 是「本人第几次开口」（决定轮到哪条机制理由），
    ``skeleton_step`` 是「全场第几条发言」（决定用哪套句式）——分开之后，
    四位专家各自的第一次发言才不会同时落到同一套句式上。
    """
    spread = int(skeleton_step if skeleton_step is not None else step)
    evidence = _evidence_at(pack, step)
    # 每条发言轮换一条机制理由：第一次开口用席位论据内核，之后换别的维度，
    # 否则同一个人三轮都说同一句「反馈越快练得越准」
    reason_span = min(len(pack.reasons), 3)
    reason = pack.reasons[step % reason_span]
    rest = [item for item in pack.reasons[1:] if item and item != reason]
    body = pack.consequence
    if not body and len(rest) > 1:
        body, rest = rest[0], rest[1:]
    # 展开句和论据内核常常是同一句（域素材本来就这么写），撞了只在一处说
    echo = bool(body) and (body[:6] in reason or reason[:6] in body)

    lead = ""
    if name:
        lead_head, _ = _skeleton(
            ADDRESS_OPPOSE if oppose else ADDRESS_ALLY,
            ("",),
            kind="address",
            step=spread,
            offset=pack.offset,
            used=used,
        )
        lead = _fill(lead_head, name=name, keyword=keyword)
    head, head_tail = _skeleton(
        STATEMENT_HEADS,
        STATEMENT_TAILS,
        kind="statement",
        step=spread + (1 if name else 0),
        offset=pack.offset,
        used=used,
    )
    # 视角词放在判断句内部：长句被截断时也不会丢掉「这句是谁的」
    judgement = (
        lead
        + _fill(head, keyword=keyword)
        + pack.judgement
        + _fill(head_tail, angle=STATEMENT_ANGLES[pack.offset % len(STATEMENT_ANGLES)])
    )

    reason_head, reason_tail = _skeleton(
        REASON_HEADS,
        REASON_TAILS,
        kind="reason",
        step=spread + 1,
        offset=pack.offset,
        used=used,
    )
    support = _fill(reason_head, reason=reason, keyword=keyword) + _fill(
        reason_tail, evidence=evidence
    )
    parts = [judgement]
    # 顺序很关键：判断 → 机制理由（含席位论据）→ 展开句 → 补充理由。
    # 超长时 ``_fit`` 从末尾往前砍，先砍补充与展开，绝不把席位论据砍掉。
    parts.append(support)
    if body and not echo:
        parts.append(f"{body}。")
    # 多垫一条机制理由：同一位专家连着说两轮，内容不只是换了句式的同一句
    if rest:
        extra = rest[step % len(rest)]
        ev_head, ev_tail = _skeleton(
            EVIDENCE_HEADS,
            EVIDENCE_TAILS,
            kind="evidence",
            step=spread + 2,
            offset=pack.offset,
            used=used,
        )
        parts.append(
            extra.rstrip("。")
            + "；"
            + _fill(
                ev_head,
                scene=pack.scene,
                evidence=_evidence_other(pack, evidence, step),
                keyword=keyword,
            )
            + ev_tail
        )
    return parts


def _clash_parts(
    pack: ArgumentPack,
    keyword: str,
    name: str,
    key: str,
    attack: str,
    step: int,
    used: set[str] | None = None,
    skeleton_step: int | None = None,
    opponent_text: str = "",
) -> list[str]:
    """交锋层：复述对方「关键判断词」（≤10 字）→ 立刻转折 → 自己的机制与场景。

    这里**不再引用对方整句**：``key`` 由 ``key_claim`` 产出、最长 10 字，
    既保留了「点名 + 复述判断」的交锋感，又不会变成逐字复读。
    """
    # 例证要避开对方刚说过的句子，否则「反驳」里会出现和他一样的例子（读起来像复读）
    pool = [item for item in pack.evidence if item and item not in str(opponent_text or "")]
    evidence = pool[(2 * step + len(key) + len(attack)) % len(pool)] if pool else _evidence_at(
        pack, 2 * step + len(key) + len(attack)
    )
    head, tail = _skeleton(
        CLASH_HEADS,
        CLASH_TAILS,
        kind="clash",
        step=int(skeleton_step if skeleton_step is not None else step),
        offset=pack.offset,
        used=used,
    )
    if key:
        head_text = _fill(head, name=name, key=key, keyword=keyword)
    else:
        head_text = _fill(
            ADDRESS_OPPOSE[step % len(ADDRESS_OPPOSE)], name=name, keyword=keyword
        )
    return [head_text + _fill(tail, attack=attack, evidence=evidence)]


def _deepen_parts(
    pack: ArgumentPack,
    keyword: str,
    name: str,
    step: int,
    used: set[str] | None = None,
    skeleton_step: int | None = None,
) -> list[str]:
    """深化层：换一条机制维度往下推，保持同一立场但不再重复第一轮。"""
    span = max(1, len(pack.reasons) - 1)
    index = min(1 + (pack.offset + step) % span, len(pack.reasons) - 1)
    reason = pack.reasons[index]
    evidence = _evidence_at(pack, step + index)
    spread = int(skeleton_step if skeleton_step is not None else step)
    if name:
        head, tail = _skeleton(
            DEEPEN_NAMED_HEADS,
            DEEPEN_TAILS,
            kind="deepen-named",
            step=spread,
            offset=pack.offset,
            used=used,
        )
        head_text = _fill(head, name=name, keyword=keyword)
    else:
        head, tail = _skeleton(
            DEEPEN_HEADS,
            DEEPEN_TAILS,
            kind="deepen",
            step=spread,
            offset=pack.offset,
            used=used,
        )
        head_text = _fill(head, keyword=keyword)
    return [head_text + _fill(tail, reason=reason, evidence=evidence)]


def _correction_parts(
    pack: ArgumentPack,
    keyword: str,
    step: int,
    used: set[str] | None = None,
    skeleton_step: int | None = None,
) -> list[str]:
    """修正层：有条件让步 + 守住判断 + 一条没被驳倒的机制。"""
    span = max(1, len(pack.reasons) - 1)
    index = min(1 + (pack.offset + step + 1) % span, len(pack.reasons) - 1)
    reason = pack.reasons[index]
    evidence = _evidence_at(pack, step + 1 + index)
    keep = _plain_judgement(pack)
    head, tail = _skeleton(
        CONCESSION_HEADS,
        CONCESSION_TAILS,
        kind="concession",
        step=int(skeleton_step if skeleton_step is not None else step),
        offset=pack.offset,
        used=used,
    )
    return [
        _fill(head, keyword=keyword)
        + _fill(tail, concession=pack.concession, keep=keep, reason=reason, evidence=evidence)
    ]


def _clip(text: str, limit: int = SPEECH_LIMIT) -> str:
    """按整句截断，绝不把一句话切成半截（「教育这一层绕不」这种）。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    for index in range(len(window) - 1, 0, -1):
        if window[index] in "。！？":
            return window[: index + 1]
    return window.rstrip("，、；：,.;: ") + "。"


def _fit(parts: list[str], pack: ArgumentPack, keyword: str) -> str:
    """压到 40~120 字：先砍展开句，再砍末句，最后整句截断；并兜住「咬议题 + 有锚点」。"""
    sentences = [part for part in parts if part]
    text = "".join(sentences)
    if len(text) > SPEECH_LIMIT and sentences and "——" in sentences[0]:
        head = sentences[0].split("——")[0].rstrip()
        sentences[0] = head if head.endswith("。") else f"{head}。"
        text = "".join(sentences)
    while len(text) > SPEECH_LIMIT and len(sentences) > 1:
        sentences = sentences[:-1]
        text = "".join(sentences)
    text = _clip(text)
    # 议题关键词必须出现：缺了就补机制，而不是留一句「放之四海皆准」的话
    if keyword and keyword not in text:
        pad = pack.reasons[0].split("，")[0]
        text = _clip(f"{text.rstrip('。')}；{keyword}上，{pad}。")
    # 具体锚点（场景 / 时间线 / 结构性数字）至少要出现一个
    anchors = (pack.scene, *pack.evidence)
    if not any(anchor and anchor in text for anchor in anchors):
        text = _clip(f"{text.rstrip('。')}；放到{pack.scene}里看，{_evidence_at(pack, 1)}。")
    if len(text) < SPEECH_FLOOR:
        pad = pack.consequence or (pack.reasons[1] if len(pack.reasons) > 1 else "")
        if pad:
            text = _clip(f"{text.rstrip('。')}；{keyword}这一层绕不开{pad}。")
    return _clip(text)


def compose_speech(
    *,
    topic: str,
    intent: str = "抢答",
    speaker: str = "",
    profession: str = "",
    stance: str = "",
    reply_to: str = "",
    recent: object = (),
    step: int = 0,
    turn: int = 1,
    total_rounds: int = 20,
    expert_count: int = 4,
    used_skeletons: set[str] | None = None,
) -> str:
    """生成一条 Mock 发言（议题参数化 + 分层论证 + 句式去重）。

    ``used_skeletons`` 由调用方（讨论主循环）按场次维护：同一场里每类句式
    尽量只用一次；纯函数调用（测试 / 调试）不传，则按 ``step`` 稳定轮转。
    """
    keyword = primary_keyword(topic)
    pack = argument_pack(topic, profession, stance)
    phase = phase_for(turn, total_rounds, expert_count)
    #: 句式轮换用「席位稳定种子 + step」：同一人第 n 次开口必然换一种说法
    turn_rng = random.Random(f"{topic}|{speaker}|{int(step or 0)}|{int(turn or 0)}")

    parts: list[str]
    opponent, opponent_text = pick_opponent_speech(recent, reply_to, speaker)
    if opponent == speaker:
        opponent, opponent_text = "", ""
    #: 同阵营的人被点名时不许说「我不同意」：改成顺着他的话补充
    ally = bool(opponent_text) and same_side(topic, opponent_text, pack.stance)

    if phase == PHASE_STATEMENT:
        # 只有「反驳 / 补充」才接过话头；「抢答」不该先提别人
        target = opponent if intent in ("反驳", "补充") else ""
        parts = _statement_parts(
            pack,
            keyword,
            turn_rng,
            step,
            target,
            oppose=intent == "反驳" and not ally,
            used=used_skeletons,
            skeleton_step=turn,
        )
    elif phase == PHASE_CLASH:
        key = key_claim(opponent_text) if opponent_text else ""
        # 同一个判断词全场只复述一次：第二次再被点名就只点名、换自己的机制，
        # 否则屏幕上是四句「你把话说到「谈不上有利」」
        if key and used_skeletons is not None:
            marker = f"key:{key}"
            if marker in used_skeletons:
                key = ""
            else:
                used_skeletons.add(marker)
        if opponent and not ally:
            # 拿不到可复述的判断词时仍保留交锋：只点名 + 抓漏洞 + 自己的例证
            attack = attack_line(
                topic,
                opponent_text or "",
                pack.stance,
                turn_rng,
                step=step,
                avoid=used_skeletons,
            )
            parts = _clash_parts(
                pack,
                keyword,
                opponent,
                key,
                attack,
                step,
                used=used_skeletons,
                skeleton_step=turn,
                opponent_text=opponent_text,
            )
        elif opponent:
            # 同阵营被点名：顺着补充，不编造对立
            parts = _statement_parts(
                pack,
                keyword,
                turn_rng,
                step,
                opponent,
                oppose=False,
                used=used_skeletons,
                skeleton_step=turn,
            )
        else:
            parts = _deepen_parts(
                pack, keyword, "", step, used=used_skeletons, skeleton_step=turn
            )
    elif phase == PHASE_CORRECTION:
        parts = _correction_parts(
            pack, keyword, step, used=used_skeletons, skeleton_step=turn
        )
    else:
        parts = _deepen_parts(
            pack, keyword, opponent, step, used=used_skeletons, skeleton_step=turn
        )
    return _fit(parts, pack, keyword)


__all__ = [
    "ArgumentPack",
    "CONCESSION_HEADS",
    "CONCESSION_TAILS",
    "CLASH_HEADS",
    "CLASH_TAILS",
    "DEEPEN_HEADS",
    "DEEPEN_NAMED_HEADS",
    "DomainArguments",
    "EVIDENCE_HEADS",
    "GENERAL_ARGUMENTS",
    "KEY_CLAIM_LIMIT",
    "PHASES",
    "PHASE_CLASH",
    "PHASE_CORRECTION",
    "PHASE_DEEPEN",
    "PHASE_STATEMENT",
    "REASON_HEADS",
    "Reason",
    "SKELETON_KINDS",
    "SPEECH_FLOOR",
    "SPEECH_LIMIT",
    "STATEMENT_HEADS",
    "SideArguments",
    "advice_for",
    "argument_pack",
    "arguments_for_topic",
    "attack_line",
    "common_ground_for",
    "compose_speech",
    "deadlock_for",
    "identify_seat",
    "judgement_phrase",
    "key_claim",
    "phase_for",
    "pick_opponent_speech",
    "primary_keyword",
    "question_form",
    "quote_clause",
    "same_side",
    "stance_core",
]
















