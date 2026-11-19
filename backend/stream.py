"""SSE 广播中枢：每场讨论一个 ``asyncio.Queue``。

为什么需要这一层
----------------
讨论由后台任务推进（``discussion.py``），HTTP 连接随时可能晚到、断开或重连；
把「产出事件」与「消费事件」拆开之后：

- **供给端**只往队列里写，不关心有没有人听（讨论不会因为没人看而卡住）；
- **消费端**从队列里读，先补历史（``backlog``）再收实时事件；
- ``done`` 是终止事件，订阅到这里即结束；``close()`` 让尚无 ``done`` 的流也能正常收尾；
- ``Last-Event-ID`` 用 ``seq`` 水位实现「重连不重复」。

六种事件（``message`` / ``status`` / ``consensus`` / ``phase`` / ``done``）都以
``schemas.SseEvent`` 判别联合的形式入队，本模块不校验也不改写事件内容。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final

from backend.schemas import SseEvent

#: 单场事件回放上限：超出后丢最旧的（讨论本身早已结束，重放只用于刷新页面）
MAX_BACKLOG: Final[int] = 1000

#: 队列关闭哨兵（``None``）：消费到它就结束订阅
_CLOSED: Final[None] = None


@dataclass
class StreamChannel:
    """一场讨论的事件通道：一个队列 + 一份可回放的 backlog。"""

    session_id: str
    max_backlog: int = MAX_BACKLOG
    queue: "asyncio.Queue[SseEvent | None]" = field(default_factory=asyncio.Queue)
    backlog: list[SseEvent] = field(default_factory=list)
    closed: bool = False

    def push(self, event: SseEvent) -> None:
        """入队并记入 backlog；通道已关闭时丢弃（迟到事件不得污染已结束的流）。"""
        if self.closed:
            return
        if len(self.backlog) >= self.max_backlog:
            del self.backlog[0]
        self.backlog.append(event)
        self.queue.put_nowait(event)

    def finish(self) -> None:
        """关闭通道：之后 ``subscribe`` 读完剩余事件即返回。"""
        if self.closed:
            return
        self.closed = True
        self.queue.put_nowait(_CLOSED)


class StreamBroker:
    """按 ``session_id`` 管理通道（进程级单例见 ``broker``）。"""

    def __init__(self, *, max_backlog: int = MAX_BACKLOG) -> None:
        self._max_backlog = max_backlog
        self._channels: dict[str, StreamChannel] = {}

    # ------------------------------------------------------------ 通道管理

    def channel(self, session_id: str) -> StreamChannel:
        """取（或创建）某场讨论的通道。"""
        channel = self._channels.get(session_id)
        if channel is None:
            channel = StreamChannel(session_id=session_id, max_backlog=self._max_backlog)
            self._channels[session_id] = channel
        return channel

    def is_open(self, session_id: str) -> bool:
        """该场讨论是否仍在产出事件。"""
        channel = self._channels.get(session_id)
        return channel is not None and not channel.closed

    def has_backlog(self, session_id: str) -> bool:
        """是否已有可回放的历史事件（用于判断「不重启讨论、只回放」）。"""
        channel = self._channels.get(session_id)
        return bool(channel and channel.backlog)

    def reset(self) -> None:
        """清空全部通道（测试 / 进程重启复用）。"""
        self._channels.clear()

    # ------------------------------------------------------------ 供给端

    def publish(self, session_id: str, event: SseEvent) -> None:
        """推送一个事件（非阻塞；讨论任务在事件循环内直接调用）。"""
        self.channel(session_id).push(event)

    def close(self, session_id: str) -> None:
        """结束该场讨论的事件流。"""
        self.channel(session_id).finish()

    # ------------------------------------------------------------ 消费端

    async def subscribe(
        self,
        session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SseEvent]:
        """订阅事件流：先回放 backlog（跳过 ``last_event_id`` 之前），再收实时事件。

        ``done`` 或通道关闭即结束；客户端断开时生成器被取消，无需额外退订。
        """
        channel = self.channel(session_id)
        watermark = 0

        for event in list(channel.backlog):
            watermark = max(watermark, event.seq)
            if last_event_id is not None and event.seq <= last_event_id:
                continue
            yield event
            if event.event == "done":
                return

        while True:
            event = await channel.queue.get()
            if event is _CLOSED:
                return
            if event.seq <= watermark:
                continue
            watermark = event.seq
            yield event
            if event.event == "done":
                return


#: 进程级单例：讨论任务与 SSE 端点共用同一个中枢
broker = StreamBroker()


__all__ = ["MAX_BACKLOG", "StreamBroker", "StreamChannel", "broker"]
