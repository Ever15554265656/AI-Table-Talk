"""路由 1/4：会话（发起 / 列表）。

对应 ``docs/API.md``：

- ``POST /api/sessions`` —— 建会话 + 生成阵容，返回 201 ``SessionDetail``；
- ``GET  /api/sessions`` —— 首页 / 回看列表（按创建时间倒序，可按状态过滤、可分页）。

列表页的两个计数（``expert_total`` / ``message_count``）用一次聚合查询补齐，
避免「每条列表项再查一次」的 N+1。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func

from backend.database import SessionLocal
from backend.flows.lineup import LineupError, generate_lineup
from backend.models import DiscussionSession, Expert, Message, SessionStatus
from backend.routers.errors import lineup_failed, session_not_found
from backend.schemas import ExpertOut, SessionCreate, SessionDetail, SessionListItem

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _detail(
    session: DiscussionSession,
    experts: list[Expert],
    message_count: int,
) -> SessionDetail:
    return SessionDetail(
        id=session.id,
        topic=session.topic,
        expert_count=session.expert_count,
        status=session.status,
        created_at=session.created_at,
        expert_total=len(experts),
        message_count=message_count,
        experts=[ExpertOut.model_validate(expert) for expert in experts],
    )


def _order_host_first(db, session_id: str) -> list[Expert]:
    """契约顺序：主持人在前，其余按创建顺序（席位环渲染依赖此顺序）。"""
    return (
        db.query(Expert)
        .filter(Expert.session_id == session_id)
        .order_by(Expert.is_host.desc(), Expert.id)
        .all()
    )


@router.post("", response_model=SessionDetail, status_code=201)
async def create_session(payload: SessionCreate) -> SessionDetail:
    """发起一场讨论：建会话 → 生成阵容 → 返回详情（此时仍未开演）。"""
    with SessionLocal() as db:
        session = DiscussionSession(
            topic=payload.topic,
            expert_count=payload.expert_count,
            status=SessionStatus.CREATED.value,
        )
        db.add(session)
        db.commit()

        try:
            generate_lineup(db, session)
        except LineupError as exc:
            # 阵容失败不留下「空壳会话」，避免首页出现无法开演的条目
            db.delete(session)
            db.commit()
            raise lineup_failed(str(exc)) from exc

        return _detail(session, _order_host_first(db, session.id), 0)


@router.get("", response_model=list[SessionListItem])
async def list_sessions(
    status: SessionStatus | None = Query(
        default=None, description="按状态过滤（created / live / ended）"
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[SessionListItem]:
    """首页 / 回看列表：创建时间倒序（同刻按 id 倒序，保证顺序稳定）。"""
    with SessionLocal() as db:
        query = db.query(DiscussionSession).order_by(
            DiscussionSession.created_at.desc(), DiscussionSession.id.desc()
        )
        if status is not None:
            query = query.filter(DiscussionSession.status == status.value)
        sessions = query.offset(offset).limit(limit).all()
        if not sessions:
            return []

        ids = [session.id for session in sessions]
        expert_counts = dict(
            db.query(Expert.session_id, func.count(Expert.id))
            .filter(Expert.session_id.in_(ids))
            .group_by(Expert.session_id)
            .all()
        )
        message_counts = dict(
            db.query(Message.session_id, func.count(Message.id))
            .filter(Message.session_id.in_(ids))
            .group_by(Message.session_id)
            .all()
        )

        return [
            SessionListItem(
                id=session.id,
                topic=session.topic,
                expert_count=session.expert_count,
                status=session.status,
                created_at=session.created_at,
                expert_total=int(expert_counts.get(session.id, 0)),
                message_count=int(message_counts.get(session.id, 0)),
            )
            for session in sessions
        ]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str) -> SessionDetail:
    """单场详情（前端刷新后恢复演播厅用）。"""
    with SessionLocal() as db:
        session = db.get(DiscussionSession, session_id)
        if session is None:
            raise session_not_found(session_id)
        count = int(
            db.query(func.count(Message.id))
            .filter(Message.session_id == session_id)
            .scalar()
            or 0
        )
        return _detail(session, _order_host_first(db, session_id), count)


__all__ = ["router"]
