"""HTTP 层：五组路由，只做「校验 → 调流程 → 序列化」。

=====================  ============================================================
模块                    端点
=====================  ============================================================
``sessions``            ``POST /api/sessions`` · ``GET /api/sessions`` · ``GET /api/sessions/{id}``
``experts``             ``GET /api/sessions/{id}/experts``
``messages``            ``GET /api/sessions/{id}/messages``
``insights``            ``GET /api/sessions/{id}/insights``
``stream``              ``GET /api/stream/{id}``（兼容 ``/api/sessions/{id}/events``）
=====================  ============================================================

``API_ROUTERS`` 是注册顺序清单：``main.create_app`` 只遍历它，避免手工逐个 include 时漏挂。
"""

from __future__ import annotations

from backend.routers import experts, insights, messages, sessions, stream

#: 需要挂到 FastAPI 上的路由（顺序即注册顺序）
API_ROUTERS = (
    sessions.router,
    experts.router,
    messages.router,
    insights.router,
    stream.router,
)

__all__ = ["API_ROUTERS", "experts", "insights", "messages", "sessions", "stream"]
