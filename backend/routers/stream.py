"""路由 4/4：SSE 端点（``GET /api/stream/{id}``）。

一次连接做三件事：

1. **校验**：会话不存在直接 404（不建立长连接）；
2. **开演**：``start_discussion`` 幂等地确保后台任务在跑（重复连接只回放，不重跑）；
3. **转发**：把广播中枢的事件逐帧 ``encode_sse`` 输出，``done`` 之后自然收尾。

旧路径 ``/api/sessions/{id}/events`` 保留（不进 OpenAPI），前端迁移期两者等价。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from backend.database import SessionLocal
from backend.discussion import start_discussion
from backend.models import DiscussionSession
from backend.routers.errors import session_not_found
from backend.schemas import encode_sse
from backend.stream import broker

router = APIRouter(prefix="/api", tags=["stream"])

#: 关掉各级代理缓冲，否则前端会「一次性收到全部事件」
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _ensure_session(session_id: str) -> None:
    with SessionLocal() as db:
        if db.get(DiscussionSession, session_id) is None:
            raise session_not_found(session_id)


def _parse_last_event_id(
    request: Request,
    explicit: int | None,
    header_value: str | None,
) -> int | None:
    """``Last-Event-ID`` 水位：优先查询参数，其次请求头，解析失败按「无水位」处理。"""
    if explicit is not None:
        return explicit
    raw = header_value or request.headers.get("last-event-id")
    if not raw:
        return None
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return None


async def _frames(session_id: str, last_event_id: int | None) -> AsyncIterator[str]:
    async for event in broker.subscribe(session_id, last_event_id=last_event_id):
        yield encode_sse(event)


async def _stream(
    session_id: str, request: Request, explicit: int | None, header_value: str | None
) -> StreamingResponse:
    _ensure_session(session_id)
    start_discussion(session_id)
    return StreamingResponse(
        _frames(session_id, _parse_last_event_id(request, explicit, header_value)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/stream/{session_id}")
async def stream_session(
    session_id: str,
    request: Request,
    last_event_id: int | None = Query(
        default=None, ge=0, description="断线重连水位（等价于 Last-Event-ID 请求头）"
    ),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """实时事件流：``message`` / ``status`` / ``consensus`` / ``phase`` / ``done``。"""
    return await _stream(session_id, request, last_event_id, last_event_id_header)


@router.get("/sessions/{session_id}/events", include_in_schema=False)
async def stream_session_legacy(
    session_id: str,
    request: Request,
    last_event_id: int | None = Query(default=None, ge=0),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """兼容旧路径（与 ``/api/stream/{id}`` 完全等价）。"""
    return await _stream(session_id, request, last_event_id, last_event_id_header)


__all__ = ["SSE_HEADERS", "router"]
