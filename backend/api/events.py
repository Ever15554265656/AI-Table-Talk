"""旧的 SSE 端点模块（保留导入路径兼容）。

端点实现已迁移到 ``backend.routers.stream``：

- 新路径 ``GET /api/stream/{session_id}``；
- 旧路径 ``GET /api/sessions/{session_id}/events`` 仍可用（等价实现）。

本模块只做再导出，请勿在此继续加逻辑（``main`` 已不再据此注册路由）。
"""

from __future__ import annotations

from backend.routers.stream import SSE_HEADERS, router

__all__ = ["SSE_HEADERS", "router"]

