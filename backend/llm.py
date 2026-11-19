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
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from functools import lru_cache
from typing import Any

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

#: 专家池（按席位顺序取前 N 位，立场覆盖支持 / 反对 / 有条件）
_MOCK_GUESTS: tuple[dict[str, str], ...] = (
    {
        "name": "林岚",
        "profession": "组织行为学",
        "title": "某高校副教授",
        "stance": "四天工作制只在知识型岗位成立，而且必须先改考核方式",
    },
    {
        "name": "周衡",
        "profession": "云计算",
        "title": "某云厂商首席架构师",
        "stance": "工时缩短会先压垮交付节奏，成本最后转嫁给一线",
    },
    {
        "name": "郑弈",
        "profession": "劳动法",
        "title": "某律所合伙人",
        "stance": "缺少工时与加班认定细则，企业会把它变成隐性加班",
    },
    {
        "name": "罗屿",
        "profession": "宏观经济学",
        "title": "某研究院研究员",
        "stance": "生产率提升是前提，否则只是把工资换成休息时间",
    },
    {
        "name": "韩澈",
        "profession": "制造业管理",
        "title": "某工厂运营总监",
        "stance": "产线不能停，缩工时只会变成排班表上的数字游戏",
    },
    {
        "name": "陆准",
        "profession": "产品设计",
        "title": "某互联网公司产品负责人",
        "stance": "真正被压缩的是返工和会议，不是有效工作时间",
    },
)

#: 开场 / 收尾（``speak`` 场景的 purpose 变体）
_MOCK_OPENING: str = (
    "先把争点收窄：如果四天工作制真能提高效率，那多出来的收益该归谁？"
    "我想听到具体机制，而不是口号。"
)
_MOCK_CLOSING: str = (
    "今天至少有一点清楚了：争论已经不在「要不要缩短工时」，而在「成本由谁承担」。"
)

#: 总结池：三段（空行分隔）对应「共识 / 分歧 / 结论建议」，与 ``flows.summary`` 的切分规则对齐
_MOCK_SUMMARY: str = (
    "这一场真正谈拢的，是四天工作制能不能成立取决于两件事：产出是否可衡量，考核是否同步改。"
    "\n\n"
    "没谈拢的是成本落到谁头上。企业担心排班与交付节奏，劳动者担心它变成隐性加班。"
    "\n\n"
    "如果产出可衡量、加班认定清楚，四天制值得小范围试；"
    "否则它只是把休息时间换成了更长的周中。"
)

#: 交锋阶段的发言池（1~2 句，≤120 字，与后端截断阈值对齐）
_MOCK_SPEECHES: tuple[str, ...] = (
    "这一条我不认。工时压到四天，交付节奏不会自己变松，只会把压力挤到周中那几个深夜。",
    "我补一个前提：只有产出可衡量的岗位才谈得上效率提升，否则就是把工作量藏起来。",
    "反馈越快，返工越少，这部分收益是真的，但它不会自动变成员工的休息时间。",
    "法律上还有个洞：工时不减、加班认定不清，四天制就会变成隐性加班的遮羞布。",
    "从成本看，企业先付的是排班成本，收益要等两个季度，所以大多数公司会半途而废。",
    "我同意方向，但不同意节奏：先把会议和审批砍掉一半，再谈四天，否则只是换个说法。",
    "如果生产率没提升，缩短工时就只是把工资换成休息，这笔账迟早要有人结。",
    "我更担心弱者：一线岗位没有议价能力，弹性工时最后只对知识型员工友好。",
)

#: 举手池：三条模板按候选顺序分配（抢答 → 补充 → 反驳），便于用例断言优先级
_MOCK_HANDS: tuple[dict[str, str], ...] = (
    {"intent": "抢答", "focus": "想先把「效率提升」和「成本转移」拆成两个问题"},
    {"intent": "补充", "focus": "要补一个前提：只在知识型岗位成立"},
    {"intent": "反驳", "focus": "反对把交付压力转嫁给一线"},
)

#: 共识 / 分歧池：``__g0__`` / ``__g1__`` 由真实专家名替换，``__stranger__`` 故意是幻觉姓名
_MOCK_INSIGHTS: tuple[dict[str, Any], ...] = (
    {
        "type": "共识",
        "content": "四天工作制的效果高度依赖岗位类型与考核方式",
        "speaker_names": ("__g0__", "__g1__"),
    },
    {
        "type": "分歧",
        "content": "缩短工时的成本应由企业还是员工承担",
        "speaker_names": ("__g0__", "__stranger__"),
    },
)

#: 幻觉姓名：服务端必须把它剔除（保证 ``expert_ids`` 只含本场真实席位）
_MOCK_STRANGER_NAME: str = "未在场的评论员"


def _mock_lineup_text(payload: Mapping[str, Any]) -> str:
    """按 ``expert_count`` 从专家池取前 N 位，颜色由调色板顺序分配。"""
    count = int(payload.get("expert_count") or 4)
    palette = [str(color) for color in (payload.get("palette") or PANEL_COLOR_PALETTE)]
    seats = [{**_MOCK_HOST, "color": palette[0]}]
    seats.extend(
        {
            **_MOCK_GUESTS[index % len(_MOCK_GUESTS)],
            "color": palette[(index + 1) % len(palette)],
        }
        for index in range(count)
    )
    return json.dumps(seats, ensure_ascii=False)


def _mock_turn_taking_text(payload: Mapping[str, Any]) -> str:
    """按候选顺序分配三种意图，举手内容引用真实的候选人姓名。"""
    candidates = [item for item in (payload.get("candidates") or []) if item.get("name")]
    last_speaker = str(payload.get("last_speaker_name") or "")
    hands = [
        {
            "name": candidate["name"],
            "intent": template["intent"],
            "reply_to": "" if template["intent"] == "抢答" else last_speaker,
            "focus": template["focus"],
        }
        for candidate, template in zip(candidates, _MOCK_HANDS, strict=False)
    ]
    return json.dumps({"raised_hands": hands}, ensure_ascii=False)


def _mock_speak_text(payload: Mapping[str, Any]) -> str:
    """开场 / 收尾走专用文案，交锋阶段按 ``turn`` 轮转发言池。"""
    purpose = str(payload.get("purpose") or "debate")
    if purpose == "opening":
        return _MOCK_OPENING
    if purpose == "closing":
        return _MOCK_CLOSING
    if purpose == "summary":
        return _MOCK_SUMMARY
    turn = max(1, int(payload.get("turn") or 1))
    return _MOCK_SPEECHES[(turn - 1) % len(_MOCK_SPEECHES)]


def _mock_summary_text(payload: Mapping[str, Any]) -> str:
    """收尾总结（独立场景）：固定三段，保证「共识 / 分歧 / 结论建议」都有内容。"""
    return _MOCK_SUMMARY


def _mock_consensus_text(payload: Mapping[str, Any]) -> str:
    """用本场真实专家名替换占位符，并保留一个幻觉姓名供服务端剔除。"""
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
            "content": template["content"],
            "speaker_names": [mapping.get(name, name) for name in template["speaker_names"]],
        }
        for template in _MOCK_INSIGHTS
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
            if index and self._latency_ms:
                time.sleep(self._latency_ms / 1000)
            yield chunk

    async def _mock_astream(
        self, scene: str, payload: Mapping[str, Any]
    ) -> AsyncIterator[str]:
        for index, chunk in enumerate(_chunk_text(mock_text(scene, payload))):
            if index and self._latency_ms:
                await asyncio.sleep(self._latency_ms / 1000)
            yield chunk

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
        """系统提示词 + 用户消息（运行期数据一律放用户消息，便于日志脱敏）。"""
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
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
