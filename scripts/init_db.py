"""初始化数据库并写入样例数据。

用法::

    python scripts/init_db.py            # 建表；库为空时写入样例
    python scripts/init_db.py --reset    # 删表重建后写入样例（本地重置常用）

关于样例数据（PRD R12）：

- 全部样例**只存在于本脚本**，应用代码中不预设任何题库；
- 覆盖 6 个话题，满足交付要求「≥5 条高质量样例数据（含预设讨论话题与对应嘉宾阵容）」；
- 每场都包含 1 位主持人（``is_host=True``）+ N 位立场互斥的专家；
- 颜色按 ``PANEL_COLOR_PALETTE`` 顺序分配，保证同场唯一；
- 覆盖 ``created`` / ``live`` / ``ended`` 三种状态，其中 ``ended`` 的样例带完整发言与共识，
  便于验收「只读回放」与「总结展示」而不必先跑满一场讨论。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 允许 `python scripts/init_db.py` 直接运行（无需先安装为包）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402

from backend.config import PANEL_COLOR_PALETTE, settings  # noqa: E402
from backend.database import SessionLocal, create_all, reset_database  # noqa: E402
from backend.models import (  # noqa: E402
    ConsensusItem,
    ConsensusType,
    DiscussionSession,
    Expert,
    Message,
    SessionStatus,
    utc_now,
)

#: 样例数据。``messages`` 元素为 ``(speaker_key, content)``，speaker_key 取 ``host`` / ``e1..eN``；
#: ``consensus`` 元素为 ``(ConsensusType, content, [speaker_key, ...])``。
SAMPLE_SESSIONS: list[dict] = [
    {
        "id": "smp-remote-innovation",
        "topic": "远程办公是否降低了团队的创新力？",
        "status": SessionStatus.CREATED,
        "minutes_ago": 260,
        "host": {
            "name": "沈知远",
            "profession": "财经媒体",
            "title": "资深圆桌主持人",
            "stance": "保持中立，只负责开场、追问与串联，不预设立场",
        },
        "experts": [
            {
                "name": "林岚",
                "profession": "组织行为学",
                "title": "某高校副教授",
                "stance": "远程办公抹掉了走廊里的偶发碰撞，创新产出确实在下降",
            },
            {
                "name": "郑野",
                "profession": "互联网",
                "title": "分布式团队负责人",
                "stance": "远程把人才池扩大了十倍，创意来源反而更杂更活",
            },
            {
                "name": "何砚",
                "profession": "人力资源咨询",
                "title": "首席顾问",
                "stance": "关键不在远程还是线下，而在有没有结构化的同步机制",
            },
            {
                "name": "苏黎",
                "profession": "创业公司",
                "title": "创始人兼 CEO",
                "stance": "早期团队必须线下磨合，规模化之后才有资格谈远程",
            },
        ],
        "messages": [],
        "consensus": [],
    },
    {
        "id": "smp-ai-copyright",
        "topic": "AI 生成内容是否应享有著作权保护？",
        "status": SessionStatus.CREATED,
        "minutes_ago": 190,
        "host": {
            "name": "顾言",
            "profession": "科技法律",
            "title": "知识产权律师·圆桌主持",
            "stance": "不表态，负责把法律与产业两套语言翻译给观众",
        },
        "experts": [
            {
                "name": "唐璇",
                "profession": "法学",
                "title": "版权法教授",
                "stance": "现有独创性标准足以覆盖 AI 辅助创作，无需另立新法",
            },
            {
                "name": "白桦",
                "profession": "产业研究",
                "title": "版权产业高级研究员",
                "stance": "应新设邻接权，保护投入而非人格，否则产业无法融资",
            },
            {
                "name": "江越",
                "profession": "内容平台",
                "title": "版权运营总监",
                "stance": "平台需要的是可执行的快速确权机制，而不是十年诉讼",
            },
            {
                "name": "马骁",
                "profession": "开源社区",
                "title": "核心维护者",
                "stance": "过度保护会直接锁死创作生态，最后只剩巨头能玩",
            },
            {
                "name": "温婉",
                "profession": "视觉创作",
                "title": "自由插画师",
                "stance": "训练数据未授权就是侵权的起点，保护应从这里开始",
            },
        ],
        "messages": [],
        "consensus": [],
    },
    {
        "id": "smp-four-day-week",
        "topic": "公司是否应全面推行四天工作制？",
        "status": SessionStatus.LIVE,
        "minutes_ago": 42,
        "host": {
            "name": "邵宁",
            "profession": "管理咨询",
            "title": "圆桌主持人",
            "stance": "中立，但对成本数字追问到底",
        },
        "experts": [
            {
                "name": "韩澈",
                "profession": "劳动经济",
                "title": "研究员",
                "stance": "生产率完全支撑得住，工时从来不是产出的瓶颈",
            },
            {
                "name": "韦岚",
                "profession": "制造业",
                "title": "生产总监",
                "stance": "产线连续性决定四天制在制造业几乎没有落地空间",
            },
            {
                "name": "秦舟",
                "profession": "科技公司",
                "title": "人力资源副总裁",
                "stance": "先做弹性试点，用半年数据再谈全面推行",
            },
            {
                "name": "罗见",
                "profession": "中小企业",
                "title": "创始人",
                "stance": "客户响应窗口缩短会直接损失订单，小公司扛不住",
            },
            {
                "name": "夏熙",
                "profession": "心理咨询",
                "title": "临床心理学家",
                "stance": "过长工时的隐性成本被严重低估，倦怠才是最大支出",
            },
            {
                "name": "杜衡",
                "profession": "劳动法",
                "title": "执业律师",
                "stance": "合规改造与薪酬重算的成本远高于多数人预期",
            },
        ],
        "messages": [
            ("host", "欢迎各位，今天只讨论一个问题：四天工作制，到底是效率红利还是成本转移？"),
            ("e1", "我用三个行业的跟踪数据说话：压缩工时后单位产出没有下降，反而下降了请假率。"),
            ("e2", "这个结论放到产线上就不成立了，我们的设备开机成本是按天摊销的。"),
            ("e4", "我补充一个更现实的约束，客户不会因为我们周三不上班就把需求往后挪。"),
        ],
        "consensus": [],
    },
    {
        "id": "smp-short-video-attention",
        "topic": "短视频是否高估了对青少年注意力的伤害？",
        "status": SessionStatus.LIVE,
        "minutes_ago": 18,
        "host": {
            "name": "章序",
            "profession": "媒体",
            "title": "圆桌主持人",
            "stance": "中立，要求每位嘉宾给出可验证的证据",
        },
        "experts": [
            {
                "name": "池悦",
                "profession": "认知神经科学",
                "title": "副教授",
                "stance": "注意力并未被破坏，只是被重新分配到了新的刺激源",
            },
            {
                "name": "岑野",
                "profession": "基础教育",
                "title": "中学校长",
                "stance": "课堂专注度下降是一线教师十年来的共同观察",
            },
            {
                "name": "阮清",
                "profession": "平台算法",
                "title": "推荐系统工程师",
                "stance": "推荐机制确实放大沉浸，但工程上完全可以设边界",
            },
            {
                "name": "傅安",
                "profession": "发展心理学",
                "title": "教授",
                "stance": "关键不问短视频有多长，而问它替代了哪些发展性活动",
            },
        ],
        "messages": [
            ("host", "先请各位用一句话回答：伤害是被高估了，还是被低估了？"),
            ("e1", "现有实验证据并不支持「注意力被永久损伤」这个强结论，更多是可逆的分配变化。"),
            ("e2", "但可逆不等于无害，一个学期下来学生的深度阅读能力是肉眼可见地退化的。"),
            ("e4", "我同意退化的观察，但要把归因放到「它挤掉了什么」上，而不是屏幕时长本身。"),
        ],
        "consensus": [],
    },
    {
        "id": "smp-open-model",
        "topic": "开源大模型会终结闭源商业模型吗？",
        "status": SessionStatus.ENDED,
        "minutes_ago": 1500,
        "host": {
            "name": "陆准",
            "profession": "科技产业",
            "title": "圆桌主持人",
            "stance": "中立，最后负责收束双方论点",
        },
        "experts": [
            {
                "name": "沈拓",
                "profession": "大模型研究",
                "title": "首席科学家",
                "stance": "开源会吃掉长尾，闭源只能守住前沿与工程化",
            },
            {
                "name": "郑弈",
                "profession": "云计算",
                "title": "解决方案架构师",
                "stance": "企业最终买的是确定性与合规，不是权重本身",
            },
            {
                "name": "白露",
                "profession": "开源基金会",
                "title": "项目负责人",
                "stance": "生态迭代速度才是开源真正的护城河",
            },
            {
                "name": "简舟",
                "profession": "投资机构",
                "title": "合伙人",
                "stance": "商业模型会被重构，但不会消失，价值会向上游迁移",
            },
            {
                "name": "高岑",
                "profession": "芯片",
                "title": "系统架构师",
                "stance": "推理成本下降才是真正的分水岭，模型是否开源自始不是重点",
            },
            {
                "name": "洛宁",
                "profession": "学术研究",
                "title": "助理教授",
                "stance": "评测透明度比开源与否更影响采购决策",
            },
        ],
        "messages": [
            ("host", "今晚只讨论一件事：开源大模型会不会终结闭源商业模型？请先给结论，再给理由。"),
            ("e1", "我的结论是不会终结，但会压缩：通用能力上闭源的优势窗口只剩十二到十八个月。"),
            ("e2", "我不同意压缩这个判断，企业采购的第一诉求是可用性与责任边界，不是权重能不能下载。"),
            ("e3", "那要看你怎么定义生态，开源社区的迭代速度是闭源团队人数的百倍量级。"),
            ("e4", "从投资视角看，价值正在从模型层往推理层和数据层迁移，这才是真正的变化。"),
            ("e5", "我补充一个被低估的变量：推理成本每下降一个数量级，商业模型就要重算一次。"),
            ("e1", "成本下降对开源的利好更大，因为开源可以自由做蒸馏和量化裁剪。"),
            ("e2", "但企业要的是 SLA 和审计，这两样东西开源社区今天给不了，明天也很难给。"),
            ("e6", "我插一句，采购方真正卡的是评测透明度，闭源至少能提供可复核的评测报告。"),
            ("e3", "评测报告也可以开源复现，事实上我们社区已经把主流榜单全部重跑过一遍了。"),
            ("e4", "所以更可能的终局是分层：通用模型被开源压价，垂直场景被闭源吃掉。"),
            ("host", "最后请每位用一句话收尾：你希望自己的公司明天用开源还是闭源？"),
        ],
        "consensus": [
            (
                ConsensusType.AGREE,
                "闭源模型在长上下文、工程化交付与责任承担上的优势，短期难以被开源追平",
                ["e1", "e2"],
            ),
            (
                ConsensusType.AGREE,
                "推理成本下降会持续重构商业模式，任何一方都不能靠既有价格维持优势",
                ["e4", "e5"],
            ),
            (
                ConsensusType.AGREE,
                "开源不等于免费，企业仍会为确定性、合规与服务付费",
                ["e2", "e3"],
            ),
            (
                ConsensusType.DISAGREE,
                "开源是否会吃掉通用模型市场：一方认为长尾已经失守，另一方认为企业只为确定性与合规付费",
                ["e1", "e3", "e2"],
            ),
            (
                ConsensusType.DISAGREE,
                "评测透明度与开源权重哪个更影响采购决策，双方未能达成一致",
                ["e6", "e1"],
            ),
        ],
    },
    {
        "id": "smp-hukou-policy",
        "topic": "一线城市是否应继续放宽落户门槛？",
        "status": SessionStatus.CREATED,
        "minutes_ago": 95,
        "host": {
            "name": "严澈",
            "profession": "公共政策",
            "title": "圆桌主持人",
            "stance": "中立，要求区分短期财政与长期人口结构",
        },
        "experts": [
            {
                "name": "闻笛",
                "profession": "城市规划",
                "title": "研究员",
                "stance": "人口结构压力之下，放宽落户是必要而非可选",
            },
            {
                "name": "卓野",
                "profession": "财政研究",
                "title": "副研究员",
                "stance": "公共服务承载力才是真正约束，放宽必须与财政同步",
            },
            {
                "name": "舒迟",
                "profession": "人口学",
                "title": "教授",
                "stance": "应做分层放宽：先技能与年龄，再全面放开",
            },
        ],
        "messages": [],
        "consensus": [],
    },
]

# ---------------------------------------------------------------- 构建与写入


def build_session(
    spec: dict, now: datetime
) -> tuple[DiscussionSession, list[Expert], list[Message], list[ConsensusItem]]:
    """把一条样例规格转换为 ORM 对象（不落库）。

    席位 id 采用 ``{session_id}-p{序号}``，既便于调试，也保证
    「按 (is_host desc, id) 排序」即可稳定还原主持人在前、专家按席位的顺序。
    """
    session_id: str = spec["id"]
    created_at = now - timedelta(minutes=int(spec["minutes_ago"]))
    palette = list(PANEL_COLOR_PALETTE)

    session = DiscussionSession(
        id=session_id,
        topic=spec["topic"],
        expert_count=len(spec["experts"]),
        status=spec["status"].value,
        created_at=created_at,
    )

    host = Expert(
        id=f"{session_id}-p00",
        session_id=session_id,
        is_host=True,
        color=palette[0],
        **spec["host"],
    )
    seats: list[Expert] = [host]
    speaker_by_key: dict[str, Expert] = {"host": host}

    for index, data in enumerate(spec["experts"], start=1):
        if index >= len(palette):
            raise ValueError(f"{session_id}: 专家数量超出配色板容量（{len(palette)} 色）")
        expert = Expert(
            id=f"{session_id}-p{index:02d}",
            session_id=session_id,
            is_host=False,
            color=palette[index],
            **data,
        )
        seats.append(expert)
        speaker_by_key[f"e{index}"] = expert

    messages = [
        Message(
            id=f"{session_id}-m{round_no:02d}",
            session_id=session_id,
            expert_id=speaker_by_key[key].id,
            content=content,
            round=round_no,
            created_at=created_at + timedelta(seconds=40 * round_no),
        )
        for round_no, (key, content) in enumerate(spec["messages"], start=1)
    ]

    items = [
        ConsensusItem(
            id=f"{session_id}-c{index:02d}",
            session_id=session_id,
            type=item_type.value,
            content=content,
            expert_ids=[speaker_by_key[key].id for key in keys],
            created_at=created_at + timedelta(seconds=90 * index),
        )
        for index, (item_type, content, keys) in enumerate(spec["consensus"], start=1)
    ]

    return session, seats, messages, items


def seed(with_reset: bool = False) -> int:
    """建表并写入样例数据；库中已有讨论时跳过（除非 ``--reset``）。"""
    if with_reset:
        reset_database()
    else:
        create_all()

    with SessionLocal() as db:
        existing = db.scalar(select(func.count()).select_from(DiscussionSession)) or 0
        if existing:
            print(f"[skip] 数据库已有 {existing} 场讨论，未写入样例。")
            print("       如需重建：python scripts/init_db.py --reset")
            return 0

        now = utc_now()
        for spec in SAMPLE_SESSIONS:
            session, seats, messages, items = build_session(spec, now)
            # 显式逐个 add，避免依赖关系级联的隐式行为
            db.add(session)
            db.add_all(seats)
            db.add_all(messages)
            db.add_all(items)
        db.commit()

        session_total = db.scalar(select(func.count()).select_from(DiscussionSession)) or 0
        expert_total = db.scalar(select(func.count()).select_from(Expert)) or 0
        message_total = db.scalar(select(func.count()).select_from(Message)) or 0
        item_total = db.scalar(select(func.count()).select_from(ConsensusItem)) or 0

    print(f"[ok] 数据库：{settings.database_url}")
    print(
        f"[ok] 写入 {session_total} 场讨论 / {expert_total} 个席位 / "
        f"{message_total} 条发言 / {item_total} 条共识或分歧"
    )
    for spec in SAMPLE_SESSIONS:
        print(
            f"     - {spec['id']:<26} {spec['status'].value:<8} "
            f"专家 {len(spec['experts'])} 人 | 发言 {len(spec['messages'])} 条 "
            f"| 共识分歧 {len(spec['consensus'])} 条 | {spec['topic']}"
        )

    if session_total < 5:
        print(f"[warn] 样例数量为 {session_total}，低于交付要求的下限 5 条")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化 AI Panel Studio 的 SQLite 数据库")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先删除所有表再重建（会清空现有数据）",
    )
    args = parser.parse_args(argv)
    return seed(with_reset=args.reset)


if __name__ == "__main__":
    raise SystemExit(main())
