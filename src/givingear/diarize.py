"""录音文件按说话人转写（givingear diarize）：不联网、不接 AI 模型，全部在本机完成。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .audio import WavFileSource
from .backend import NullBackend
from .events import EventBus
from .transcript import TranscriptLog


async def diarize(path: str | Path, names: dict[str, str] | None = None) -> dict:
    """返回 {"lines": [...], "speakers": [...]}。names 可以预先给说话人起名，例如 {"说话人 1": "小王"}。"""
    from .session import Session

    source = WavFileSource(path)  # 先读文件：读不了就不必加载模型
    bus = EventBus()
    log = TranscriptLog()
    q = bus.subscribe()
    session = await asyncio.to_thread(Session, NullBackend(), bus)
    await session.run(source)
    while not q.empty():
        log.handle(q.get_nowait())
    for ref, new in (names or {}).items():
        profile = session.profiles.find(ref)
        if profile:
            log.names[profile.id] = new
    return {"lines": log.query(clock="audio"), "speakers": log.speakers()}
