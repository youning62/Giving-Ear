"""对外事件流：核心发生的每件事都变成一个事件，外壳（命令行、以后的图形界面和 MCP）只订阅事件。"""

from __future__ import annotations

import asyncio
import time
from typing import Any


def event(type_: str, audio_t: float | None = None, **fields: Any) -> dict[str, Any]:
    """事件统一为可直接转成 JSON 的字典。audio_t 是音频时间轴上的时间，wall_t 是发出事件时的系统时间。"""
    e: dict[str, Any] = {"type": type_, "wall_t": round(time.time(), 3)}
    if audio_t is not None:
        e["audio_t"] = round(audio_t, 3)
    e.update(fields)
    return e


class EventBus:
    """一对多广播。每个订阅者有自己的队列，慢的订阅者不会拖住核心。"""

    def __init__(self, max_queue: int = 1000):
        self._subs: set[asyncio.Queue] = set()
        self._max_queue = max_queue
        self.history: list[dict[str, Any]] = []

    def publish(self, e: dict[str, Any]) -> None:
        self.history.append(e)
        for q in list(self._subs):
            if q.full():
                q.get_nowait()  # 丢掉最旧的，保证新事件能进来
            q.put_nowait(e)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(self._max_queue)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def close(self) -> None:
        self.publish(event("closed"))
