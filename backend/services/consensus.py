"""兼容层：实现已迁移至 ``backend.flows.consensus``。

保留本模块只为不破坏既有导入路径。新代码请直接
``from backend.flows.consensus import ...``。
"""

from __future__ import annotations

from backend.flows.consensus import (
    CONTENT_MAX_LEN,
    TYPE_MAPPING,
    ConsensusError,
    build_consensus_payload,
    extract_insights,
)

__all__ = [
    "CONTENT_MAX_LEN",
    "TYPE_MAPPING",
    "ConsensusError",
    "build_consensus_payload",
    "extract_insights",
]
