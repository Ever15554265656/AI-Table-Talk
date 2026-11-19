"""LLM 输出的 JSON 解析：模型常把 JSON 包进 ``` 代码块，或在前后加一句解释。

流程层共用工具：``lineup`` / ``turn_taking`` / ``consensus`` 都要求模型返回 JSON，
但模型的「礼貌用语」（如「以下是阵容：」）不该让整场讨论失败，因此统一在这里容错。
"""

from __future__ import annotations

import json
import re
from typing import Any

_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class LLMParseError(ValueError):
    """模型输出无法解析为 JSON（上层据此重试或降级）。"""


def strip_code_fence(text: str) -> str:
    """去掉 Markdown 代码围栏，返回其中的 JSON 片段。"""
    match = _CODE_FENCE.search(text)
    return (match.group(1) if match else text).strip()


def _slice_bracket(text: str) -> str | None:
    """截取首个 ``[`` / ``{`` 到最后一个 ``]`` / ``}``，容忍前后缀噪声。"""
    starts = [index for index in (text.find("["), text.find("{")) if index >= 0]
    if not starts:
        return None
    ends = [index for index in (text.rfind("]"), text.rfind("}")) if index >= 0]
    if not ends:
        return None
    start, end = min(starts), max(ends)
    return text[start : end + 1] if end > start else None


def loads_json(text: str) -> Any:
    """尽力把模型输出解析成 Python 对象；失败抛 :class:`LLMParseError`。"""
    candidate = strip_code_fence(text)
    for attempt in (candidate, _slice_bracket(candidate)):
        if not attempt:
            continue
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    preview = candidate[:80].replace("\n", " ")
    raise LLMParseError(f"模型输出不是合法 JSON：{preview!r}")


def clean_text(text: str, *, prefixes: tuple[str, ...] = ()) -> str:
    """清洗上屏文本：去首尾空白、去「姓名：」前缀、去包裹引号。"""
    result = text.strip()
    for prefix in prefixes:
        if prefix and result.startswith(prefix):
            result = result[len(prefix) :].lstrip()
            break
    return result.strip().strip("\"“”「」『』").strip()


__all__ = ["LLMParseError", "clean_text", "loads_json", "strip_code_fence"]
