"""兼容层：实现已迁移至 ``backend.flows.turn_taking``。

保留本模块只为不破坏既有导入路径。新代码请直接
``from backend.flows.turn_taking import ...``。
"""

from __future__ import annotations

from backend.flows.turn_taking import (
    DEFAULT_FOCUS,
    FOCUS_MAX_LEN,
    INTENTS,
    INTENT_PRIORITY,
    TurnDecision,
    TurnTakingError,
    build_turn_payload,
    select_next_speaker,
)

__all__ = [
    "DEFAULT_FOCUS",
    "FOCUS_MAX_LEN",
    "INTENTS",
    "INTENT_PRIORITY",
    "TurnDecision",
    "TurnTakingError",
    "build_turn_payload",
    "select_next_speaker",
]
