"""TDD 用例：``backend/stream.py`` 的 SSE 队列管理。

约束（对应需求 2）：
- 每场讨论一个 ``asyncio.Queue``，推送 ``message / status / consensus / phase / done`` 五种事件；
- 供给与消费解耦：讨论先跑完、客户端后连上也要能收到完整事件（回放）；
- ``done`` 是终止事件，订阅到此结束；
- 支持 ``Last-Event-ID``：重连时跳过已收到的事件（按 ``seq`` 水位）。

这些用例全部是「发布 → 订阅」的纯内存行为，不启动任何讨论。
"""

from __future__ import annotations

import asyncio

from backend.models import SessionStatus
from backend.schemas import (
    DiscussionPhase,
    DoneEvent,
    DoneEventData,
    PhaseEvent,
    PhaseEventData,
)
from backend.stream import StreamBroker

SESSION_ID = "session-test"


def _phase(seq: int, session_id: str = SESSION_ID) -> PhaseEvent:
    return PhaseEvent(
        session_id=session_id,
        seq=seq,
        data=PhaseEventData(phase=DiscussionPhase.DEBATE, round=seq, max_rounds=6),
    )


def _done(seq: int, session_id: str = SESSION_ID) -> DoneEvent:
    return DoneEvent(
        session_id=session_id,
        seq=seq,
        data=DoneEventData(
            status=SessionStatus.ENDED, total_rounds=6, message_count=8
        ),
    )


def test_channel_uses_one_asyncio_queue():
    """每场讨论只维护一个队列（发布端入队、订阅端出队）。"""
    broker = StreamBroker()

    channel = broker.channel(SESSION_ID)

    assert isinstance(channel.queue, asyncio.Queue)
    assert broker.channel(SESSION_ID) is channel, "同一场必须复用同一个 channel"
    assert broker.channel("other") is not channel


async def test_publish_then_close_delivers_in_order():
    """先发布再关闭：订阅者按序收到全部事件，队列耗尽即结束。"""
    broker = StreamBroker()
    broker.publish(SESSION_ID, _phase(1))
    broker.publish(SESSION_ID, _phase(2))
    broker.close(SESSION_ID)

    received = [event async for event in broker.subscribe(SESSION_ID)]

    assert [event.seq for event in received] == [1, 2]
    assert broker.is_open(SESSION_ID) is False


async def test_subscription_stops_at_done():
    """``done`` 之后的事件不再下发（终止语义由事件本身保证）。"""
    broker = StreamBroker()
    broker.publish(SESSION_ID, _phase(1))
    broker.publish(SESSION_ID, _done(2))
    broker.publish(SESSION_ID, _phase(3))
    broker.close(SESSION_ID)

    received = [event async for event in broker.subscribe(SESSION_ID)]

    assert [event.event for event in received] == ["phase", "done"]


async def test_late_subscriber_replays_backlog():
    """讨论先跑完、客户端后连上：回放全部事件。"""
    broker = StreamBroker()
    for seq in (1, 2, 3):
        broker.publish(SESSION_ID, _phase(seq))
    broker.close(SESSION_ID)

    received = [event async for event in broker.subscribe(SESSION_ID)]

    assert [event.seq for event in received] == [1, 2, 3]


async def test_last_event_id_skips_acknowledged_events():
    """断线重连：``Last-Event-ID`` 之前的事件不重复下发。"""
    broker = StreamBroker()
    for seq in (1, 2, 3):
        broker.publish(SESSION_ID, _phase(seq))
    broker.close(SESSION_ID)

    received = [
        event async for event in broker.subscribe(SESSION_ID, last_event_id=2)
    ]

    assert [event.seq for event in received] == [3]


async def test_live_events_are_delivered_while_running():
    """订阅期间新发布的事件实时到达，随后 close 结束订阅。"""
    broker = StreamBroker()
    broker.publish(SESSION_ID, _phase(1))

    stream = broker.subscribe(SESSION_ID)
    first = await anext(stream)
    broker.publish(SESSION_ID, _phase(2))
    broker.close(SESSION_ID)
    rest = [event async for event in stream]

    assert [first.seq, *[event.seq for event in rest]] == [1, 2]


async def test_publish_after_close_is_ignored():
    """关闭后的迟到事件不得进入队列（避免污染已结束的流）。"""
    broker = StreamBroker()
    broker.close(SESSION_ID)

    broker.publish(SESSION_ID, _phase(1))

    received = [event async for event in broker.subscribe(SESSION_ID)]
    assert received == []


async def test_reset_clears_all_channels():
    """``reset`` 供测试与进程重启复用：清空队列与回放，不残留上一场事件。"""
    broker = StreamBroker()
    broker.publish(SESSION_ID, _phase(1))
    broker.reset()

    assert broker.is_open(SESSION_ID) is False

    broker.publish(SESSION_ID, _phase(1))
    broker.close(SESSION_ID)
    received = [event async for event in broker.subscribe(SESSION_ID)]

    assert [event.seq for event in received] == [1]
