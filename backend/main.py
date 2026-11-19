"""FastAPI 应用入口。

路由全部来自 ``backend.routers.API_ROUTERS``（会话 / 席位 / 回看 / SSE），
``/api/health`` 只暴露脱敏信息（Mock 开关、模型名、轮次上限），绝不返回密钥。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.llm import get_llm
from backend.routers import API_ROUTERS


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

