"""路由 3/5：回看共识（``GET /api/sessions/{id}/insights``）。

对应 ``docs/API.md`` §3.4：把一场讨论的共识 / 分歧按 ``type`` 分成两组返回，
字段与 SSE ``consensus`` 事件里的 ``ConsensusItemOut`` **完全同源**（同一个 Pydantic 类），
前端回放与直播因此可以共用同一套渲染逻辑。

顺序：``created_at`` 升序（同刻按 id），保证同一份快照每次返回顺序一致。
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.database import SessionLocal
from backend.models import ConsensusItem, ConsensusType, DiscussionSession
from backend.routers.errors import session_not_found
from backend.schemas import ConsensusItemOut, InsightsOut

router = APIRouter(prefix="/api/sessions", tags=["insights"])


@router.get("/{session_id}/insights", response_model=InsightsOut)
async def list_insights(session_id: str) -> InsightsOut:
    """整组快照：``agree`` / ``disagree`` 各自最新一版。"""
    with SessionLocal() as db:
        if db.get(DiscussionSession, session_id) is None:
            raise session_not_found(session_id)

        items = (
            db.query(ConsensusItem)
            .filter(ConsensusItem.session_id == session_id)
            .order_by(ConsensusItem.created_at, ConsensusItem.id)
            .all()
        )

    agree = [
        ConsensusItemOut.model_validate(item)
        for item in items
        if item.type == ConsensusType.AGREE.value
    ]
    disagree = [
        ConsensusItemOut.model_validate(item)
        for item in items
        if item.type == ConsensusType.DISAGREE.value
    ]
    return InsightsOut(session_id=session_id, agree=agree, disagree=disagree)


__all__ = ["router"]
