"""兼容层：实现已迁移至 ``backend.flows.lineup``。

保留本模块只为不破坏既有导入路径。新代码请直接 ``from backend.flows.lineup import ...``。
"""

from __future__ import annotations

from backend.flows.lineup import (
    SEAT_FIELDS,
    LineupError,
    build_lineup_payload,
    generate_lineup,
)

__all__ = [
    "SEAT_FIELDS",
    "LineupError",
    "build_lineup_payload",
    "generate_lineup",
]
