"""清理开发库里的历史会话（换议题后避免「回看旧测试场」被误认为没修好）。

用法（在项目根执行）::

    python scripts/clean_sessions.py                # 交互确认后删除全部会话
    python scripts/clean_sessions.py --keep 3       # 只保留最近 3 场，其余删除
    python scripts/clean_sessions.py --yes          # 跳过确认（CI / 脚本里用）

只删「讨论数据」（sessions / experts / messages / consensus_items），
不动表结构，删完可以直接从首页新开一场。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.database import SessionLocal  # noqa: E402
from backend.models import (  # noqa: E402
    ConsensusItem,
    DiscussionSession,
    Expert,
    Message,
)


def _session_ids(db, keep: int) -> list[str]:
    """按创建时间倒序，跳过最近 ``keep`` 场，返回要删的会话 id。"""
    rows = (
        db.query(DiscussionSession.id)
        .order_by(DiscussionSession.created_at.desc(), DiscussionSession.id.desc())
        .all()
    )
    ids = [row[0] for row in rows]
    return ids[max(0, keep) :]


def clean(keep: int = 0, *, assume_yes: bool = False) -> dict[str, int]:
    """删除历史会话，返回各表删除条数（便于脚本断言 / 日志）。"""
    db = SessionLocal()
    try:
        ids = _session_ids(db, keep)
        if not ids:
            return {"sessions": 0, "experts": 0, "messages": 0, "insights": 0}
        if not assume_yes:
            print(f"将删除 {len(ids)} 场会话（保留最近 {keep} 场）。继续？[y/N] ", end="")
            if input().strip().lower() not in {"y", "yes"}:
                print("已取消，未删除任何数据。")
                return {"sessions": 0, "experts": 0, "messages": 0, "insights": 0}

        # 先删子表，再删会话：SQLite 默认不级联
        messages = db.query(Message).filter(Message.session_id.in_(ids)).delete(
            synchronize_session=False
        )
        experts = db.query(Expert).filter(Expert.session_id.in_(ids)).delete(
            synchronize_session=False
        )
        insights = db.query(ConsensusItem).filter(
            ConsensusItem.session_id.in_(ids)
        ).delete(synchronize_session=False)
        sessions = db.query(DiscussionSession).filter(
            DiscussionSession.id.in_(ids)
        ).delete(synchronize_session=False)
        db.commit()
        return {
            "sessions": int(sessions),
            "experts": int(experts),
            "messages": int(messages),
            "insights": int(insights),
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="清理历史讨论会话（只删数据，不改表结构）")
    parser.add_argument("--keep", type=int, default=0, help="保留最近 N 场（默认 0 = 全删）")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args()

    removed = clean(max(0, args.keep), assume_yes=args.yes)
    print(
        "已清理：会话 {sessions} 场 / 席位 {experts} 条 / 发言 {messages} 条 / 共识 {insights} 条".format(
            **removed
        )
    )


if __name__ == "__main__":
    main()
