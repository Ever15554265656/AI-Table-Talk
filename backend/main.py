"""FastAPI 应用入口。

路由全部来自 ``backend.routers.API_ROUTERS``（会话 / 席位 / 回看 / SSE），
``/api/health`` 只暴露脱敏信息（Mock 开关、模型名、轮次上限），绝不返回密钥。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 只影响模块导入路径，不改任何业务逻辑。兼容两种启动方式：
# 1) 仓库根目录：``uvicorn backend.main:app`` → ``__package__ == "backend"``，本段跳过，
#    行为与改动前完全一致；
# 2) backend 目录：``uvicorn main:app`` → ``__package__ == ""``，此时 ``main`` 是顶层模块，
#    需要把项目根补回 ``sys.path``，否则下面 ``from backend.config import settings``
#    会抛 ``ModuleNotFoundError: No module named 'backend'``。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.llm import get_llm  # noqa: E402
from backend.routers import API_ROUTERS  # noqa: E402


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Panel Studio API",
        version="0.1.0",
        description="AI 圆桌讨论：阵容生成、实时发言与共识提炼",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in API_ROUTERS:
        app.include_router(router)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """健康检查：只暴露脱敏信息，绝不返回密钥。"""
        llm = get_llm()
        return {
            "status": "ok",
            "mock_mode": llm.mock,
            "model": llm.model,
            "max_rounds": settings.max_rounds,
        }

    return app


app = create_app()


__all__ = ["app", "create_app"]

