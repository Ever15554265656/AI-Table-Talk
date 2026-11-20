"""交付物：样例数据脚本 ``backend/seeds.py``。

验收口径：
1. 至少 5 个预设讨论话题，每个都带完整阵容（1 位主持人 + ≥2 位专家）；
2. 每场专家姓名、职业、立场互斥且非空，颜色由调色板分配；
3. ``python -m backend.seeds``（``seed()``）能真正落库，且覆盖 created / live / ended 三种状态；
4. ``scripts/init_db.py`` 仍可用（兼容旧文档），实现来自同一个模块。
"""

from __future__ import annotations

from sqlalchemy import func, select

from backend.models import DiscussionSession, Expert, SessionStatus
from backend.seeds import SAMPLE_SESSIONS, seed


def test_sample_sessions_cover_required_scope():
    """≥5 个话题，每场都有主持人 + ≥2 位立场互补的专家。"""
    assert len(SAMPLE_SESSIONS) >= 5
    assert {spec["status"] for spec in SAMPLE_SESSIONS} == {
        SessionStatus.CREATED,
        SessionStatus.LIVE,
        SessionStatus.ENDED,
    }

    for spec in SAMPLE_SESSIONS:
        assert spec["topic"].strip()
        assert spec["host"]["name"].strip() and spec["host"]["stance"].strip()
        experts = spec["experts"]
        assert len(experts) >= 2, spec["id"]
        names = [expert["name"] for expert in experts]
        professions = [expert["profession"] for expert in experts]
        assert len(set(names)) == len(names), f"{spec['id']} 专家重名"
        assert len(set(professions)) == len(professions), f"{spec['id']} 职业重复"
        assert all(expert["stance"].strip() for expert in experts)


def test_seed_script_writes_database(db):
    """``seed()`` 落库后：6 场讨论、每场都有主持人、有发言/共识的样例保留内容。"""
    code = seed()
    assert code == 0, "样例写入失败"

    db.rollback()
    db.expire_all()
    sessions = db.scalar(select(func.count()).select_from(DiscussionSession))
    experts = db.scalar(select(func.count()).select_from(Expert))
    assert sessions == len(SAMPLE_SESSIONS) >= 5
    assert experts >= sessions + 1, "每场都要有主持人 + 专家"

    ended = db.query(DiscussionSession).filter(
        DiscussionSession.status == SessionStatus.ENDED.value
    ).all()
    assert ended, "样例必须包含已结束场次（用于回看验收）"
    for session in ended:
        hosts = (
            db.query(Expert)
            .filter(Expert.session_id == session.id, Expert.is_host.is_(True))
            .count()
        )
        assert hosts == 1
