"""路由 2/4：席位表（``GET /api/sessions/{id}/experts``）。

返回顺序即渲染顺序：**主持人在前**，其余按创建顺序 —— 前端席位环按数组下标定位，
因此顺序属于契约的一部分（``docs/API.md`` §4.1）。
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.database import SessionLocal
from backend.models import DiscussionSession, Expert
from backend.routers.errors import session_not_found
from backend.schemas import ExpertOut

router = APIRouter(prefix="/api/sessions", tags=["experts"])


@router.get("/{session_id}/experts", response_model=list[ExpertOut])
async def list_experts(session_id: str) -> list[ExpertOut]:
    """一场讨论的全部席位（含主持人）。"""
    with SessionLocal() as db:
        if db.get(DiscussionSession, session_id) is None:
            raise session_not_found(session_id)
        experts = (
            db.query(Expert)
            .filter(Expert.session_id == session_id)
            .order_by(Expert.is_host.desc(), Expert.id)
            .all()
        )
        return [ExpertOut.model_validate(expert) for expert in experts]


__all__ = ["router"]
