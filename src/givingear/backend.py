"""语音模型后端：核心通过这个接口和实时语音模型对话，具体是哪家模型可以替换。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

from .audio import SAMPLE_RATE, AudioFrame


@dataclass(frozen=True)
class SpeakerLabel:
    speaker_id: str
    name: str
    audio_t: float  # 发出标签时的音频时间
    utterance_start: float  # 这句话的开始时间

    @property
    def text(self) -> str:
        return f"[现在说话的是：{self.name}]"


@dataclass(frozen=True)
class BackendEvent:
    """后端发回核心的事情。"""

    kind: Literal["user_text", "ai_audio", "ai_text", "ai_turn_end", "interrupted", "go_away", "tool_call", "error"]
    text: str = ""
    audio: bytes = b""  # ai_audio：16 位单声道 PCM
    rate: int = 24000
    tool: str = ""  # tool_call：工具名
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


class VoiceBackend(Protocol):
    async def connect(self) -> None: ...
    async def send_audio(self, frame: AudioFrame) -> None: ...
    async def send_speaker_label(self, label: SpeakerLabel) -> None: ...
    async def send_tool_result(self, call_id: str, tool: str, result: dict[str, Any]) -> None: ...
    async def send_note(self, text: str) -> None:
        """插入一条给模型的背景说明（不是用户说的话，不应触发回复）。"""
        ...

    def events(self) -> AsyncIterator[BackendEvent]: ...
    async def close(self) -> None: ...


@dataclass
class FakeBackend:
    """测试用的假后端：不联网，只记录收到了什么；测试可以用 emit() 模拟模型发回的内容。"""

    connected: bool = False
    closed: bool = False
    audio_seconds: float = 0.0
    labels: list[SpeakerLabel] = field(default_factory=list)
    tool_results: list[tuple[str, str, dict]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _out: asyncio.Queue = field(default_factory=asyncio.Queue)

    async def connect(self) -> None:
        self.connected = True

    async def send_audio(self, frame: AudioFrame) -> None:
        self.audio_seconds += len(frame.samples) / SAMPLE_RATE

    async def send_speaker_label(self, label: SpeakerLabel) -> None:
        self.labels.append(label)

    async def send_tool_result(self, call_id: str, tool: str, result: dict[str, Any]) -> None:
        self.tool_results.append((call_id, tool, result))

    async def send_note(self, text: str) -> None:
        self.notes.append(text)

    def emit(self, e: BackendEvent) -> None:
        self._out.put_nowait(e)

    async def events(self) -> AsyncIterator[BackendEvent]:
        while True:
            yield await self._out.get()

    async def close(self) -> None:
        self.closed = True
