"""数据库层：SQLAlchemy Engine / SessionLocal / Base。

要点（对应 PRD R9「多讨论隔离」与 SQLite 并发风险）：

1. SQLite 连接统一开启 ``foreign_keys`` 与 ``WAL``，并设置 ``busy_timeout``，
   避免多房间并发写时出现 ``database is locked``；
2. ``check_same_thread=False`` 允许 FastAPI 线程池复用连接；
3. 本模块只提供**基础设施**，不包含任何业务查询；业务层一律通过 ``session_id`` 过滤。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings

SQLITE_PREFIX = "sqlite:///"


def _ensure_sqlite_directory(database_url: str) -> None:
    """SQLite 文件型数据库的父目录不存在时自动创建。"""
    if not database_url.startswith(SQLITE_PREFIX):
        return
    raw_path = database_url[len(SQLITE_PREFIX) :]
    if not raw_path or raw_path.startswith(":memory:") or raw_path.startswith("file:"):
        return
    Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _install_sqlite_pragmas(engine: Engine, database_url: str) -> None:
    """每个新连接建立时应用 PRAGMA（SQLite 的 PRAGMA 是连接级的）。"""
    if not database_url.startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=10000")
        finally:
            cursor.close()


def build_engine(database_url: str | None = None, *, echo: bool | None = None) -> Engine:
    """构建 Engine。测试可传入 ``sqlite://`` 内存库或临时文件库。"""
    url = database_url or settings.database_url
    _ensure_sqlite_directory(url)
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 10.0}
    engine = create_engine(
        url,
        echo=settings.sql_echo if echo is None else echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    _install_sqlite_pragmas(engine, url)
    return engine


#: 全局 Engine（进程内单例）
engine: Engine = build_engine()

#: 会话工厂。``expire_on_commit=False`` 让提交后仍可安全读取 ORM 属性（便于序列化）。
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


# ---------------------------------------------------------------- 会话工具


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级会话，异常时回滚，结束时关闭。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """后台任务（调度器 / 提炼器）使用的会话上下文。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------- 建表工具


def create_all(bind: Engine | None = None) -> None:
    """按 ``Base.metadata`` 建表（幂等：已存在的表跳过）。"""
    from backend import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(bind=bind or engine)


def drop_all(bind: Engine | None = None) -> None:
    """删除全部表（仅用于本地重置与测试）。"""
    from backend import models  # noqa: F401

    Base.metadata.drop_all(bind=bind or engine)


def reset_database(bind: Engine | None = None) -> None:
    """先删后建，用于 ``scripts/init_db.py --reset``。"""
    drop_all(bind)
    create_all(bind)


__all__ = [
    "Base",
    "SessionLocal",
    "build_engine",
    "create_all",
    "drop_all",
    "engine",
    "get_db",
    "reset_database",
    "session_scope",
]
