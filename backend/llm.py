"""LLM 客户端：openai SDK 封装 + Mock 数据池。

设计要点
--------
1. **唯一模型出口**：上层（``services/``）只依赖 ``LLMClient`` 的四个方法
   ``stream / complete / astream / acomplete``，不直接触碰 SDK，
   便于替换模型、注入测试替身（见 ``tests/conftest.py`` 的 ``llm`` 夹具）；
2. **配置只来自 ``settings``**：``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL`` / ``DEEPSEEK_MODEL``
   均由环境变量注入，本模块不出现任何密钥字面量；
3. **Mock 优先**：``settings.effective_mock_mode`` 为真（显式 ``MOCK_MODE=1`` 或未配密钥）时，
   按场景返回预设数据池，并**切成多块逐块 yield**，模拟流式 —— 让 SSE、打字机与
   TDD 用例在无网络时也能跑通；
4. **五个场景池**：``lineup`` / ``speak`` / ``turn_taking`` / ``consensus`` / ``summary``；
   ``speak`` 另有 ``opening`` / ``closing`` 两个用途变体（同一场景，复用发言池）。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from functools import lru_cache
from typing import Any

from backend import arguments
from backend import topics
from backend.config import PANEL_COLOR_PALETTE, Settings
from backend.config import settings as default_settings

# ---------------------------------------------------------------- 场景常量

SCENE_LINEUP = "lineup"
SCENE_SPEAK = "speak"
SCENE_TURN_TAKING = "turn_taking"
SCENE_CONSENSUS = "consensus"
SCENE_SUMMARY = "summary"

#: 允许调用的场景集合（拼错场景名会在调用时立即失败，而不是拿到奇怪输出）
SCENES: frozenset[str] = frozenset(
    {
        SCENE_LINEUP,
        SCENE_SPEAK,
        SCENE_TURN_TAKING,
        SCENE_CONSENSUS,
        SCENE_SUMMARY,
    }
)

#: 流式切片大小（字符）。真人打字机观感约 6~12 字/块，这里取 6 以便测试能验证分块
CHUNK_SIZE: int = 6


class LLMError(RuntimeError):
    """模型调用失败（上层据此决定重试或走兜底文案）。"""


# ---------------------------------------------------------------- Mock 数据池

#: 中立主持人（``lineup`` 池第 1 席）
_MOCK_HOST: dict[str, str] = {
    "name": "沈知远",
    "profession": "财经媒体",
    "title": "资深圆桌主持人",
    "stance": "保持中立，只负责开场、追问与串联，不预设立场",
}

#: 专家席位不再写死在本模块：职业 / Title / 立场 / 论据内核全部由
#: :mod:`backend.topics` 按**议题域**给出（技术与互联网 / 职场 / 教育 / 城市 / 青少年 + 兜底），
#: 否则任何议题都会拿到同一批职业，观众一眼就能看出阵容与议题无关。



def _topic_brief(payload: Mapping[str, Any], limit: int = 24) -> str:
    """把议题压成能自然念进一句话里的短句（议题 ≤200 字，台词 ≤120 字，必须截断）。"""
    return topics.topic_brief(str(payload.get("topic") or ""), limit)


#: 开场 / 收尾（``speak`` 场景的 purpose 变体），``{topic}`` 由真实议题填充
_MOCK_OPENING_TEMPLATE: str = (
    "今晚只讨论一个议题：「{topic}」。我把它拆成三问：现状到底是什么、"
    "代价由谁承担、什么条件下才值得推进。请各位先给判断，再给理由，别给口号。"
)
_MOCK_CLOSING_TEMPLATE: str = (
    "今天至少有一点清楚了：「{topic}」的争点不在方向，而在代价由谁承担、按什么标准验收。"
)

#: 总结不再用固定模板：``_mock_summary_text`` 直接读本场 ``transcript`` / ``insights``
#: 现场拼三段，否则主持人会把议题原文改写一遍当成共识（本轮已修复的问题）。

#: 交锋发言不再集中在这里：每个议题域都有一组「理由集 + 例证池 + 预埋弱点」
#: （见 ``backend.arguments``），发言时按轮次深度取材、用随机调度做变化，
#: 而不是从句子池里抽现成句子（抽法必然复读「这才是卡点」）。
#: 举手池：候选席位**洗牌后**依次分配（抢答 → 补充 → 反驳 循环），席位最多 8 个，
#: 所以池子要够长 —— 否则第 4 位之后的席位永远不举手，只能全程围观。
_MOCK_HANDS: tuple[dict[str, str], ...] = (
    {"intent": "抢答", "focus": "先把议题拆成现状、代价、条件三层"},
    {"intent": "补充", "focus": "要补一个被跳过的前提条件"},
    {"intent": "反驳", "focus": "反对把成本直接转嫁给一线执行者"},
    {"intent": "抢答", "focus": "想先问一个被忽略的执行细节"},
    {"intent": "补充", "focus": "补一组可对照的试点数据"},
    {"intent": "反驳", "focus": "反对用口号替代可验证的指标"},
    {"intent": "抢答", "focus": "先把各方立场对齐到同一个标准"},
    {"intent": "补充", "focus": "补一句成本到底由谁买单"},
)

#: 共识 / 分歧模板：``__g0__`` / ``__g1__`` 由真实专家名替换，``__stranger__`` 故意是幻觉姓名
_MOCK_INSIGHT_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "type": "共识",
        "content": "「{subject}」这件事上，双方都认一个前提——{ground}",
        "speaker_names": ("__g0__", "__g1__"),
    },
    {
        "type": "分歧",
        "content": "双方卡在{deadlock}",
        "speaker_names": ("__g0__", "__stranger__"),
    },
)

#: 幻觉姓名：服务端必须把它剔除（保证 ``expert_ids`` 只含本场真实席位）
_MOCK_STRANGER_NAME: str = "未在场的评论员"


def _mock_lineup_text(payload: Mapping[str, Any]) -> str:
    """按**议题全文**选角：先识别议题域，再取该域席位的职业 / Title / 立场。

    阵容不再来自「固定职业池随机抽」：议题是技术类就出技术 / 伦理 / 投资 / 劳动经济 / 教育视角，
    是教育类就出一线教师 / 教育学者 / 学生代表 / 家长代表 / 政策研究员，
    每个域内 6 个职业互斥，立场按「支持 → 反对 → 中立 → 支持 …」错开。
    """
    count = int(payload.get("expert_count") or 4)
    topic = str(payload.get("topic") or "")
    palette = [str(color) for color in (payload.get("palette") or PANEL_COLOR_PALETTE)]
    seats = [{**_MOCK_HOST, "color": palette[0]}]
    seats.extend(
        {
            "name": plan.name,
            "profession": plan.profession,
            "title": plan.title,
            "stance": plan.stance,
            "color": palette[(index + 1) % len(palette)],
        }
        for index, plan in enumerate(topics.seat_plan(topic, count))
    )
    return json.dumps(seats, ensure_ascii=False)


def _mock_turn_taking_text(payload: Mapping[str, Any]) -> str:
    """模拟「这一轮谁举手、举什么意图」：候选席位**每轮洗牌**后依次分配三种意图。

    为什么要洗牌：固定按席位顺序依次分配意图，会让「第 3 席永远举反驳」，
    屏幕上就变成一眼能看出的循环。洗牌 + 随机起点后，同一席位不同轮次的意图都会变，
    配合服务端的随机点名（``flows.turn_taking``：席位权重随机 + 立场对立加权），
    发言顺序才看不出规律。

    这里只模拟「谁举手」，**公平性由服务端保证**：``STARVATION_GAP`` 会在有人比最冷场席位
    多说了 2 轮时，强制把话筒交给最少发言的那组，避免固定模板让同两位专家霸麦。
    """
    candidates = [item for item in (payload.get("candidates") or []) if item.get("name")]
    if not candidates:
        return json.dumps({"raised_hands": []}, ensure_ascii=False)

    last_speaker = str(payload.get("last_speaker_name") or "")
    topic = str(payload.get("topic") or "")
    order = list(candidates)
    random.shuffle(order)
    start = random.randrange(len(_MOCK_HANDS))

    hands = []
    for offset, candidate in enumerate(order):
        template = _MOCK_HANDS[(start + offset) % len(_MOCK_HANDS)]
        intent = template["intent"]
        seat = topics.seat_for_profession(topic, str(candidate.get("profession") or ""))
        hands.append(
            {
                "name": str(candidate["name"]),
                "intent": intent,
                # 反驳 / 补充必须指向刚说完话的人，否则发言只能写成无指向的抬杠
                "reply_to": "" if intent == "抢答" else last_speaker,
                "focus": seat.focus if seat is not None else template["focus"],
            }
        )
    return json.dumps({"raised_hands": hands}, ensure_ascii=False)


def _mock_speak_text(payload: Mapping[str, Any]) -> str:
    """开场 / 收尾走议题模板；辩论发言交给 :mod:`backend.arguments` 做实论证合成。

    分层改造后不再从扁平句子池里抽现成句子（抽法必然复读「这才是卡点」）：
    判定当前层（陈述 / 交锋 / 深化 / 修正）→ 取席位论证包 → 用机制 + 例证现场拼装。
    论证包已在阵容生成时由「议题域 + 席位 + 立场」定死（``arguments.argument_pack``），
    发言阶段只按下标取用，所以同一席位跨轮的语气一致、论据却层层加深。
    """
    purpose = str(payload.get("purpose") or "debate")
    topic_text = str(payload.get("topic") or "")
    topic = _topic_brief(payload, 20)
    if purpose == "opening":
        return _MOCK_OPENING_TEMPLATE.format(topic=topic)
    if purpose == "closing":
        return _MOCK_CLOSING_TEMPLATE.format(topic=topic)
    if purpose == "summary":
        return _mock_summary_text(payload)

    turn = max(1, int(payload.get("turn") or 1))
    # 本人第几次开口；老调用方只给 turn 时退化为轮次，保证同一位专家不会复读同一句
    # （注意 0 是合法值 —— 第一次开口，不能用 ``or`` 兜底）
    raw_count = payload.get("spoken_count")
    step = max(0, int(raw_count)) if raw_count is not None else max(0, turn - 1)
    total_rounds = max(1, int(payload.get("total_rounds") or default_settings.max_rounds or 20))
    expert_count = max(1, int(payload.get("expert_count") or 4))
    recent = payload.get("recent")
    used = payload.get("used_skeletons")
    return arguments.compose_speech(
        topic=topic_text,
        intent=str(payload.get("intent") or "抢答"),
        speaker=str(payload.get("speaker") or ""),
        profession=str(payload.get("profession") or ""),
        stance=str(payload.get("stance") or ""),
        reply_to=str(payload.get("reply_to") or ""),
        recent=recent if isinstance(recent, (list, tuple)) else (),
        step=step,
        turn=turn,
        total_rounds=total_rounds,
        expert_count=expert_count,
        used_skeletons=used if isinstance(used, set) else None,
    )


def _mock_summary_text(payload: Mapping[str, Any]) -> str:
    """收尾总结：三段分别取自**本场真实发言与条目**，不再把议题原文改写成结论。

    * 共识段：优先用本场沉淀的共识条目，其次用两位专家原句的「共同承认」；
    * 分歧段：写清到底卡在哪儿（先后顺序 / 代价归属），而不是复述议题；
    * 建议段：给一个落在这个议题域上的可执行动作。
    """
    topic_text = str(payload.get("topic") or "")
    keyword = arguments.primary_keyword(topic_text)
    transcript = [
        item
        for item in (payload.get("transcript") or [])
        if isinstance(item, Mapping) and str(item.get("content") or "").strip()
    ]
    insights = [
        item
        for item in (payload.get("insights") or [])
        if isinstance(item, Mapping) and str(item.get("content") or "").strip()
    ]
    # 第一条是主持人开场，专家从第二条算起
    guests = [str(item.get("speaker") or "").strip() for item in transcript[1:]]
    guests = [name for name in guests if name]
    quotes = [arguments.quote_clause(str(item.get("content") or "")) for item in transcript[1:]]
    quotes = [quote for quote in quotes if quote]
    agree = [str(item["content"]) for item in insights if str(item.get("type")) in ("共识", "agree")]
    disagree = [str(item["content"]) for item in insights if str(item.get("type")) in ("分歧", "disagree")]

    first = guests[0] if guests else ""
    second = guests[1] if len(guests) > 1 else first
    first_quote = quotes[0] if quotes else ""
    second_quote = quotes[-1] if len(quotes) > 1 else first_quote

    if agree:
        # 共识条目已经写明「双方都认一个前提」时，换个说法，避免「谈拢的是……前提：……」两连
        lead = (
            "这一场真正谈拢的地方很具体"
            if agree[0].startswith("在「")
            else "这一场真正谈拢的是一条判断"
        )

        head = f"{lead}：{agree[0]}。"
        if first and second and first != second:
            head += f"{first}和{second}在这条上都没有反对，分歧只在措辞。"
    elif first_quote and second_quote and first != second and first_quote != second_quote:
        head = (
            f"{first}说的「{first_quote}」和{second}说的「{second_quote}」听着对立，"
            f"但两个人都把「{keyword}」当成真问题，这是本场最大的公约数。"
        )
    elif first_quote:
        # 只有一位专家留下可引原句（或两人的原句撞成同一句）时，别硬凑「A 和 A 对立」
        head = (
            f"本场被引最多的是{first}那句「{first_quote}」——"
            f"连反对的一方也拿它当靶子，「{keyword}」至少是个真问题。"
        )
    else:
        head = f"这一场没有形成可核对的共识，「{keyword}」还缺具体论据。"

    if disagree:
        body = f"没谈拢的是：{disagree[0]}。"
    else:
        # 没有沉淀出分歧条目时，用本域真正的死结，而不是所有议题都写「成本谁承担」
        body = f"没谈拢的是一道死结：{arguments.deadlock_for(topic_text)}。"
        if first and second and first != second:
            body += f"{first}要先把代价算清，{second}要先把效果做出来，两边都只肯让半步。"
        else:
            body += "两边都只肯让半步，这就是全场没走过去的地方。"

    tail = (
        f"要给观众一个能带走的判断：{arguments.advice_for(topic_text)}，「{keyword}」才推得动；"
        "在那之前，任何一边的结论都还只是立场。"
    )
    return "\n\n".join(part.strip() for part in (head, body, tail) if part.strip())


def _mock_consensus_text(payload: Mapping[str, Any]) -> str:
    """用本场真实专家名替换占位符，议题填充条目内容，并保留一个幻觉姓名供服务端剔除。

    ``subject`` / ``deadlock`` 都来自 :mod:`backend.arguments`：共识要落在「被问的这件事」上，
    分歧要写这个域真正的死结，而不是所有议题共用一句「成本该由企业、员工还是一线承担」。
    """
    topic_text = str(payload.get("topic") or "")
    keyword = arguments.primary_keyword(topic_text)
    subject = arguments.question_form(topic_text)[2] or keyword
    # 「有利」「值得推广」这类尾词太短，做共识主语读着像半句：过短就退回议题关键词
    if len(subject) < 4:
        subject = keyword
    deadlock = arguments.deadlock_for(topic_text)
    ground = arguments.common_ground_for(topic_text)
    names = [
        str(item["name"])
        for item in (payload.get("experts") or [])
        if isinstance(item, Mapping) and item.get("name")
    ]
    mapping = {
        "__g0__": names[0] if names else "",
        "__g1__": names[1] if len(names) > 1 else (names[0] if names else ""),
        "__stranger__": _MOCK_STRANGER_NAME,
    }
    items = [
        {
            "type": template["type"],
            "content": str(template["content"]).format(
                topic=_topic_brief(payload, 20),
                subject=subject,
                keyword=keyword,
                ground=ground,
                deadlock=deadlock,
            ),
            "speaker_names": [mapping.get(name, name) for name in template["speaker_names"]],
        }
        for template in _MOCK_INSIGHT_TEMPLATES
    ]
    return json.dumps(items, ensure_ascii=False)


_MOCK_BUILDERS = {
    SCENE_LINEUP: _mock_lineup_text,
    SCENE_SPEAK: _mock_speak_text,
    SCENE_TURN_TAKING: _mock_turn_taking_text,
    SCENE_CONSENSUS: _mock_consensus_text,
    SCENE_SUMMARY: _mock_summary_text,
}


def mock_text(scene: str, payload: Mapping[str, Any]) -> str:
    """返回指定场景的 Mock 全文（公开给测试与调试脚本使用）。"""
    builder = _MOCK_BUILDERS.get(scene)
    if builder is None:
        raise ValueError(f"未知场景：{scene!r}，可用：{sorted(SCENES)}")
    return builder(payload)


def _chunk_text(text: str, size: int = CHUNK_SIZE) -> Iterator[str]:
    """把全文切成固定长度的小块，模拟 token 级流式。"""
    for start in range(0, len(text), size):
        yield text[start : start + size]


# ---------------------------------------------------------------- 客户端


class LLMClient:
    """OpenAI 兼容接口的轻量封装（DeepSeek / 本地推理服务均可）。

    同步与异步两套入口并存：同步给服务层（简单、易测），异步给 SSE 引擎（不阻塞事件循环）。
    """

    def __init__(
        self,
        *,
        mock: bool | None = None,
        latency_ms: int | None = None,
        model: str | None = None,
        temperature: float | None = None,
        config: Settings | None = None,
    ) -> None:
        self._config = config or default_settings
        #: 是否走 Mock；``None`` 表示跟随配置（含「无密钥自动降级」）
        self.mock = self._config.effective_mock_mode if mock is None else mock
        self._latency_ms = self._config.mock_latency_ms if latency_ms is None else latency_ms
        self._model = model or self._config.model_name
        self._temperature = self._config.temperature if temperature is None else temperature
        self._max_tokens = self._config.max_tokens
        self._sync_client: Any = None
        self._async_client: Any = None

    # ------------------------------------------------------------ 只读属性

    @property
    def model(self) -> str:
        return self._model

    @property
    def latency_ms(self) -> int:
        return self._latency_ms

    # ------------------------------------------------------------ 公共入口

    def stream(
        self, *, scene: str, prompt: str, payload: Mapping[str, Any]
    ) -> Iterator[str]:
        """同步流式：逐块产出文本片段。"""
        self._assert_scene(scene)
        if self.mock:
            yield from self._mock_stream(scene, payload)
            return
        yield from self._remote_stream(prompt, payload)

    def complete(self, *, scene: str, prompt: str, payload: Mapping[str, Any]) -> str:
        """同步聚合：把流式片段拼成完整文本（已去除首尾空白）。"""
        return "".join(self.stream(scene=scene, prompt=prompt, payload=payload)).strip()

    async def astream(
        self, *, scene: str, prompt: str, payload: Mapping[str, Any]
    ) -> AsyncIterator[str]:
        """异步流式：SSE 端点专用，``await`` 期间不阻塞事件循环。"""
        self._assert_scene(scene)
        if self.mock:
            async for chunk in self._mock_astream(scene, payload):
                yield chunk
            return
        async for chunk in self._remote_astream(prompt, payload):
            yield chunk

    async def acomplete(
        self, *, scene: str, prompt: str, payload: Mapping[str, Any]
    ) -> str:
        """异步聚合。"""
        chunks = [
            chunk async for chunk in self.astream(scene=scene, prompt=prompt, payload=payload)
        ]
        return "".join(chunks).strip()

    # ------------------------------------------------------------ 内部：Mock

    def _assert_scene(self, scene: str) -> None:
        if scene not in SCENES:
            raise ValueError(f"未知场景：{scene!r}，可用：{sorted(SCENES)}")

    def _mock_stream(self, scene: str, payload: Mapping[str, Any]) -> Iterator[str]:
        for index, chunk in enumerate(_chunk_text(mock_text(scene, payload))):
            if index and self._delay_ms(scene):
                time.sleep(self._delay_ms(scene) / 1000)
            yield chunk

    async def _mock_astream(
        self, scene: str, payload: Mapping[str, Any]
    ) -> AsyncIterator[str]:
        for index, chunk in enumerate(_chunk_text(mock_text(scene, payload))):
            if index and self._delay_ms(scene):
                await asyncio.sleep(self._delay_ms(scene) / 1000)
            yield chunk

    def _delay_ms(self, scene: str) -> int:
        """逐块延迟只加在**上屏**的发言场景上。

        举手 / 提炼 / 总结属于内部决策（只有结果会出现在事件里），
        给它们的 JSON 也按 token 节奏 sleep 只会把整场讨论拖慢，观感反而更假。
        """
        return self._latency_ms if scene == SCENE_SPEAK else 0

    # ------------------------------------------------------------ 内部：远端

    def _ensure_sync_client(self):
        if self._sync_client is None:
            if not self._config.deepseek_api_key:
                raise LLMError("未配置 DEEPSEEK_API_KEY，无法调用真实模型（或设置 MOCK_MODE=1）")
            from openai import OpenAI  # 延迟导入：Mock 模式下完全不碰 SDK

            self._sync_client = OpenAI(
                api_key=self._config.deepseek_api_key,
                base_url=self._config.deepseek_base_url,
            )
        return self._sync_client

    def _ensure_async_client(self):
        if self._async_client is None:
            if not self._config.deepseek_api_key:
                raise LLMError("未配置 DEEPSEEK_API_KEY，无法调用真实模型（或设置 MOCK_MODE=1）")
            from openai import AsyncOpenAI

            self._async_client = AsyncOpenAI(
                api_key=self._config.deepseek_api_key,
                base_url=self._config.deepseek_base_url,
            )
        return self._async_client

    def _messages(self, prompt: str, payload: Mapping[str, Any]) -> list[dict[str, str]]:
        """系统提示词 + 用户消息（运行期数据一律放用户消息，便于日志脱敏）。

        ``used_skeletons`` 是 Mock 专用的「本场已用句式」集合：它不是模型输入，
        真实模式必须剔除（既是内部状态，也不是 JSON 可序列化类型）。
        """
        visible = {
            key: value for key, value in payload.items() if key != "used_skeletons"
        }
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(visible, ensure_ascii=False, indent=2)},
        ]

    def _remote_stream(self, prompt: str, payload: Mapping[str, Any]) -> Iterator[str]:
        client = self._ensure_sync_client()
        try:
            stream = client.chat.completions.create(
                model=self._model,
                messages=self._messages(prompt, payload),
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stream=True,
            )
            for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    yield delta
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转领域错误，交给上层降级
            raise LLMError(f"模型调用失败：{exc}") from exc

    async def _remote_astream(
        self, prompt: str, payload: Mapping[str, Any]
    ) -> AsyncIterator[str]:
        client = self._ensure_async_client()
        try:
            stream = await client.chat.completions.create(
                model=self._model,
                messages=self._messages(prompt, payload),
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stream=True,
            )
            async for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    yield delta
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"模型调用失败：{exc}") from exc


@lru_cache(maxsize=1)
def get_llm() -> LLMClient:
    """进程级单例（Mock 判定与密钥读取只做一次）。"""
    return LLMClient()


def reset_llm() -> None:
    """清空单例（测试或改了环境变量后调用）。"""
    get_llm.cache_clear()


__all__ = [
    "CHUNK_SIZE",
    "LLMClient",
    "LLMError",
    "SCENES",
    "SCENE_CONSENSUS",
    "SCENE_LINEUP",
    "SCENE_SPEAK",
    "SCENE_SUMMARY",
    "SCENE_TURN_TAKING",
    "get_llm",
    "mock_text",
    "reset_llm",
]
