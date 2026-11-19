"""HTTP 层错误：错误码与 ``docs/API.md`` 对齐，``detail`` 统一为 ``{code, message}``。"""

from __future__ import annotations

from fastapi import HTTPException

#: 会话不存在
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
#: 阵容生成失败（模型输出不可用 / 重试仍失败）
LINEUP_FAILED = "LINEUP_FAILED"


def session_not_found(session_id: str) -> HTTPException:
    """404：讨论不存在（前端据 ``code`` 跳回首页）。"""
    return HTTPException(
        status_code=404,
        detail={"code": SESSION_NOT_FOUND, "message": f"讨论不存在：{session_id}"},
    )


def lineup_failed(reason: str) -> HTTPException:
    """502：阵容生成失败（可让用户重试）。"""
    return HTTPException(
        status_code=502,
        detail={"code": LINEUP_FAILED, "message": f"阵容生成失败：{reason}"},
    )


__all__ = [
    "LINEUP_FAILED",
    "SESSION_NOT_FOUND",
    "lineup_failed",
    "session_not_found",
]
