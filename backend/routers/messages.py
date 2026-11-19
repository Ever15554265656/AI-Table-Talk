"""路由 3/4：回看发言（``GET /api/sessions/{id}/messages``）。

回看的两种用法都支持：
- 首屏拿全量（默认按 ``round`` 升序）；
- 断线 / 刷新后续读：带 ``after_round`` 只取之后的发言，天然幂等（同一轮不会重复）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.database import SessionLocal
from backend.models import DiscussionSession, Message
from backend.routers.errors import session_not_found
from backend.schemas import MessageOut

router = APIRouter(prefix="/api/sessions", tags=["messages"])


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(
    session_id: str,
    after_round: int | None = Query(
        default=None, ge=0, description="只返回轮次大于该值的发言（续读用）"
    ),
    limit: int | None = Query(
        default=None, ge=1, le=500, description="最多返回条数（默认不限）"
    ),
) -> list[MessageOut]:
    """按轮次升序返回可见发言；``after_round`` 用于增量续读。"""
    with SessionLocal() as db:
        if db.get(DiscussionSession, session_id) is None:
            raise session_not_found(session_id)

        query = (
            db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.round, Message.id)
        )
        if after_round is not None:
            query = query.filter(Message.round > after_round)
        if limit is not None:
            query = query.limit(limit)

        return [MessageOut.from_message(message) for message in query.all()]


__all__ = ["router"]
