"""E2E 全链路用例：待开演 → 开演 → 讨论中 → 结束 → 回看 → 中断恢复。

设计原则
--------
1. **走真实 HTTP / SSE 路径**（``httpx.ASGITransport`` + FastAPI app），不直接调内部函数，
   这样路由、契约、事件顺序都被覆盖；
2. **零真实 LLM**：``conftest`` 已强制 ``MOCK_MODE=1``，本文件再把 ``get_llm`` 换成
   ``RecordingLLM``，用「调用记录」证明回看 / 恢复时没有重新生成；
3. **不等真实时钟**：``SPEAK/THINK/TYPING`` 间隔在测试环境全为 0，整场 8 条发言亚秒级跑完；
4. **不猜实现**：断言都以「观众能看到的事实」为准（发言条数、谁在说、说了什么、库里有没有）。
"""

from __future__ import annotations

import httpx
from sqlalchemy import func

from backend import arguments, topics
from backend.config import settings
from backend.discussion import reset_discussions
from backend.flows.scheduling import total_rounds_for
from backend.llm import LLMClient
from backend.main import app
from backend.models import (
    ConsensusItem,
    ConsensusType,
    DiscussionSession,
    Message,
    SessionStatus,
)
from backend.stream import broker
from backend.tests.helpers import (
    parse_sse_frames,
    seed_lineup,
    seed_messages,
    seed_session,
)

#: 9 个字，短于开场白的 20 字摘要上限，因此能断言「开场引用议题原文」
TOPIC = "城市该不该继续限行"
GUESTS = 3
ROUNDS = total_rounds_for(settings.max_rounds, GUESTS)
DEBATE = ROUNDS * GUESTS
TOTAL_MESSAGES = DEBATE + 2  # 开场 + 交锋 + 收尾


class RecordingLLM(LLMClient):
    """记录每一次「生成调用」的 Mock 客户端（零网络、零真实计费）。"""

    def __init__(self) -> None:
        super().__init__(mock=True, latency_ms=0)
        self.calls: list[tuple[str, str]] = []

    def _log(self, scene: str, payload) -> None:
        purpose = str(payload.get("purpose") or "") if hasattr(payload, "get") else ""
        self.calls.append((scene, purpose))

    def complete(self, *, scene, prompt, payload):  # noqa: ANN001 - 与基类签名一致
        self._log(scene, payload)
        return super().complete(scene=scene, prompt=prompt, payload=payload)

    async def astream(self, *, scene, prompt, payload):  # noqa: ANN001
        self._log(scene, payload)
        async for chunk in super().astream(scene=scene, prompt=prompt, payload=payload):
            yield chunk

    def purposes(self) -> list[str]:
        return [purpose for _, purpose in self.calls]


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _create_session(expert_count: int = GUESTS) -> dict:
    async with _http() as client:
        response = await client.post(
            "/api/sessions", json={"topic": TOPIC, "expert_count": expert_count}
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _run_stream(session_id: str) -> list:
    """订阅一次事件流并等它自然结束（``done`` 后服务端关闭连接）。"""
    async with _http() as client:
        response = await client.get(f"/api/stream/{session_id}", timeout=30.0)
    assert response.status_code == 200
    return parse_sse_frames(response.text)


def _rows(db, session_id: str) -> list[Message]:
    db.rollback()
    db.expire_all()
    return (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.round)
        .all()
    )


def _longest_common(left: str, right: str) -> str:
    """最长公共子串：用于判定「是否照搬了对方原句」。"""
    best = ""
    for start in range(len(left)):
        end = start + len(best) + 1
        while end <= len(left) and left[start:end] in right:
            best = left[start:end]
            end += 1
    return best


# ---------------------------------------------------------------- 1. 待开演


async def test_created_session_is_ready_but_not_started(db):
    """创建后处于「待开演」：阵容已生成、立场互补、职业与配色互斥。"""
    detail = await _create_session(GUESTS)

    assert detail["status"] == SessionStatus.CREATED.value
    assert detail["topic"] == TOPIC
    assert detail["expert_count"] == GUESTS
    assert detail["message_count"] == 0, "创建阶段不许有任何发言"
    assert detail["expert_total"] == GUESTS + 1

    host = next(item for item in detail["experts"] if item["is_host"])
    guests = [item for item in detail["experts"] if not item["is_host"]]
    assert host["profession"] and host["stance"], "主持人也要有身份与立场说明"
    assert len(guests) == GUESTS
    assert len({item["profession"] for item in guests}) == GUESTS, "职业必须互斥"
    assert len({item["color"] for item in detail["experts"]}) == GUESTS + 1, "配色同场唯一"
    assert all(item["stance"].strip() for item in guests)
    labels = [topics.stance_label(item["stance"]) for item in guests]
    assert topics.stance_spread(labels), f"立场不能一边倒：{labels}"

    # 未开演：库里只有阵容，没有任何发言 / 共识
    assert _rows(db, detail["id"]) == []
    assert (
        db.query(func.count(ConsensusItem.id))
        .filter(ConsensusItem.session_id == detail["id"])
        .scalar()
        == 0
    )


# ---------------------------------------------------------------- 2. 开演


async def test_first_message_is_host_opening_quoting_the_topic(db):
    """开演：首条发言来自主持人，且明确引用议题原文。"""
    detail = await _create_session(GUESTS)
    events = await _run_stream(detail["id"])

    messages = [event.data.message for event in events if event.event == "message"]
    host = next(item for item in detail["experts"] if item["is_host"])

    assert messages[0].expert_id == host["id"], "第一条必须是主持人开场"
    assert messages[0].round == 1
    assert TOPIC in messages[0].content, "开场必须念出议题原文"
    assert messages[0].expert_name == host["name"]
    assert [event.event for event in events][0] == "phase"
    assert events[-1].event == "done"


# ---------------------------------------------------------------- 3. 讨论中


async def test_debate_is_balanced_on_topic(db):
    """讨论中：条数 / 次数均衡 / 不连说 / 咬议题（反驳单独一例）。"""
    detail = await _create_session(GUESTS)
    events = await _run_stream(detail["id"])
    messages = [event.data.message for event in events if event.event == "message"]
    host = next(item for item in detail["experts"] if item["is_host"])
    keyword = arguments.primary_keyword(TOPIC)

    # 3.1 条数：开场 1 + 交锋 ROUNDS*GUESTS + 收尾 1
    assert len(messages) == TOTAL_MESSAGES
    host_messages = [item for item in messages if item.expert_id == host["id"]]
    assert len(host_messages) == 2, "主持人只开场 + 收尾，不参与轮转"
    debate = messages[1:-1]
    assert len(debate) == DEBATE

    # 3.2 次数均衡 + 无扎堆 + 无同一人连说
    counts: dict[str, int] = {}
    for item in debate:
        counts[item.expert_id] = counts.get(item.expert_id, 0) + 1
    assert set(counts) == {item["id"] for item in detail["experts"] if not item["is_host"]}
    assert max(counts.values()) - min(counts.values()) <= 1
    speakers = [item.expert_id for item in debate]
    assert all(left != right for left, right in zip(speakers, speakers[1:]))

    # 3.3 咬议题：每条交锋发言都念到议题关键词
    missing = [item.content for item in debate if keyword not in item.content]
    assert not missing, missing


async def test_debate_rebuts_by_name_without_copying(db):
    """反驳：指名道姓，只复述 ≤10 字判断词，绝不整句照搬。

    用 2 位专家（支持 / 反对各一）跑满 3 轮：这一盘里交锋轮的对手必然是对立面，
    因此「整场至少有一条点名反驳」是可复现的事实，而不是碰运气。
    """
    detail = await _create_session(2)
    events = await _run_stream(detail["id"])
    messages = [event.data.message for event in events if event.event == "message"]
    debate = messages[1:-1]
    names = {item["name"] for item in detail["experts"]}
    host = next(item for item in detail["experts"] if item["is_host"])

    def names_someone(text: str, own: str) -> bool:
        return any(name in text for name in names | {host["name"]} if name != own)

    clashes = [
        item
        for item in debate
        if names_someone(item.content, item.expert_name)
        and any(
            mark in item.content
            for mark in (
                "不同意",
                "不接",
                "还有另一半",
                "关键判断",
                "我只认一半",
                "我补的是反面",
            )
        )
    ]
    assert clashes, "整场至少要有一条点名反驳"

    quoted = [item for item in debate if "「" in item.content and "」" in item.content]
    for item in quoted:
        key = item.content.split("「", 1)[1].split("」", 1)[0]
        assert 1 <= len(key) <= arguments.KEY_CLAIM_LIMIT, key
        # 与上一条发言比：除引号里那句判断词外，不该有更长的重合片段
        index = debate.index(item)
        previous = debate[index - 1] if index else None
        if previous is not None:
            rest = item.content.replace(f"「{key}」", "")
            assert len(_longest_common(rest, previous.content)) <= arguments.KEY_CLAIM_LIMIT


async def test_stream_emits_typing_before_every_message(db):
    """讨论中：SSE 序列里 typing 与 message 一一对应，序号连续，done 收尾。"""
    detail = await _create_session(GUESTS)
    events = await _run_stream(detail["id"])

    typing = [event for event in events if event.event == "typing"]
    messages = [event.data.message for event in events if event.event == "message"]

    assert len(typing) == len(messages), "每条发言（含开场 / 收尾）前都要有 typing"
    assert all(event.data.expert_name for event in typing)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert {event.event for event in events} == {
        "phase",
        "status",
        "typing",
        "message",
        "consensus",
        "done",
    }

    for index, event in enumerate(events):
        if event.event != "typing":
            continue
        following = next(
            (item for item in events[index + 1 :] if item.event == "message"), None
        )
        assert following is not None
        assert following.data.message.expert_id == event.data.expert_id


# ---------------------------------------------------------------- 4. 结束


async def test_finished_session_persists_summary_and_insights(db):
    """结束：状态置 ended、总结下发、共识分歧落库、条数与库一致。"""
    detail = await _create_session(GUESTS)
    events = await _run_stream(detail["id"])
    done = events[-1]

    assert done.event == "done"
    assert done.data.status == SessionStatus.ENDED.value
    assert done.data.summary is not None and done.data.summary.sections
    assert {section.heading for section in done.data.summary.sections} >= {"共识", "分歧"}
    assert done.data.degraded is False

    rows = _rows(db, detail["id"])
    assert done.data.message_count == len(rows) == TOTAL_MESSAGES
    assert [row.round for row in rows] == list(range(1, TOTAL_MESSAGES + 1))

    session = db.get(DiscussionSession, detail["id"])
    db.refresh(session)
    assert session is not None and session.status == SessionStatus.ENDED.value

    items = (
        db.query(ConsensusItem)
        .filter(ConsensusItem.session_id == detail["id"])
        .all()
    )
    assert items, "共识 / 分歧必须落库"
    assert {item.type for item in items} == {
        ConsensusType.AGREE.value,
        ConsensusType.DISAGREE.value,
    }
    assert all(item.content.strip() for item in items)
    assert all(item.expert_ids for item in items), "每条洞察都要能追到真实发言人"


# ---------------------------------------------------------------- 5. 回看


async def test_replay_is_read_only_and_never_regenerates(db, monkeypatch):
    """回看：接口只读，重复订阅只回放既有事件，零新增生成调用。"""
    detail = await _create_session(GUESTS)
    await _run_stream(detail["id"])

    before_rows = [(row.round, row.content) for row in _rows(db, detail["id"])]
    before_items = (
        db.query(func.count(ConsensusItem.id))
        .filter(ConsensusItem.session_id == detail["id"])
        .scalar()
    )

    # 回看期间任何 LLM 调用都是 bug：把工厂换成「只会记录」的桩
    recorder = RecordingLLM()
    monkeypatch.setattr("backend.discussion.get_llm", lambda: recorder)

    replay = await _run_stream(detail["id"])

    assert replay[-1].event == "done"
    assert recorder.calls == [], f"回看不许触发生成：{recorder.calls}"
    replay_messages = [event.data.message for event in replay if event.event == "message"]
    assert len(replay_messages) == len(before_rows), "回放的是既有发言，不是新发言"
    assert [(row.round, row.content) for row in _rows(db, detail["id"])] == before_rows
    assert (
        db.query(func.count(ConsensusItem.id))
        .filter(ConsensusItem.session_id == detail["id"])
        .scalar()
        == before_items
    )

    # 写入口一律被拒绝（未实现的会话动作不构成生成通道）
    async with _http() as client:
        for path in ("resume", "pause", "end", "confirm", "regenerate"):
            response = await client.post(f"/api/sessions/{detail['id']}/{path}")
            assert response.status_code in (404, 405), f"{path} 不该可写：{response.status_code}"
        still = (await client.get(f"/api/sessions/{detail['id']}")).json()
    assert still["status"] == SessionStatus.ENDED.value


# ---------------------------------------------------------------- 6. 中断恢复


async def test_restart_resumes_from_progress_without_regenerating(db, monkeypatch):
    """中断恢复：模拟进程重启后，从既有进度续播，不重放开场 / 不重复已发发言。"""
    session = seed_session(db, topic=TOPIC, expert_count=GUESTS, status="live")
    seeded = seed_lineup(db, session)
    existing = seed_messages(
        db,
        session,
        [seeded.host, seeded.guests[0], seeded.guests[1]],
        [
            f"今晚只讨论一个议题：「{TOPIC}」。先把争点摆清楚。",
            "该继续限行：短期能压住峰值，代价却落在没替代方式的人身上。",
            "不该一刀切：被挤出去的人连第二个选择都没有。",
        ],
    )
    before = [(row.round, row.expert_id, row.content) for row in existing]

    # 「重启应用」：进程内的任务登记表与广播中枢都会清空（库里的进度保留）
    reset_discussions()
    broker.reset()

    recorder = RecordingLLM()
    monkeypatch.setattr("backend.discussion.get_llm", lambda: recorder)

    events = await _run_stream(session.id)
    assert events[-1].event == "done"

    rows = _rows(db, session.id)
    assert len(rows) == TOTAL_MESSAGES, "续播后条数应与一场完整讨论一致"
    assert [row.round for row in rows] == list(range(1, TOTAL_MESSAGES + 1))
    assert [(row.round, row.expert_id, row.content) for row in rows[: len(before)]] == before, (
        "已发出的发言必须原样保留（不重新生成）"
    )
    assert before[0][2] not in [row.content for row in rows[len(before) :]]

    # 开场不重放：出现过的 purpose=opening 调用必须为 0
    assert "opening" not in recorder.purposes(), recorder.calls
    new_messages = sum(1 for event in events if event.event == "message")
    assert new_messages == TOTAL_MESSAGES - len(before)
    typing = [event for event in events if event.event == "typing"]
    assert len(typing) == new_messages, "只为新发言亮 typing，不给已播内容补占位"

    db.expire_all()
    finished = db.get(DiscussionSession, session.id)
    assert finished is not None and finished.status == SessionStatus.ENDED.value
