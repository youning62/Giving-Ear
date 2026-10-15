"""Gemini Live 后端。

两个来自验证（票 01）的要点：
- 说话人标签必须用 send_client_content(turn_complete=False) 插入；用实时文字通道插入会被当成用户说话，立刻触发回复。
- SDK 的 receive() 每收完一轮回复就结束，必须循环调用，否则消息堆积导致心跳超时断线。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from google import genai
from google.genai import types

from .audio import SAMPLE_RATE, AudioFrame
from .backend import BackendEvent, SpeakerLabel
from .prompts import SYSTEM_PROMPT
from .tools import ALL_TOOLS, ToolSpec

DEFAULT_MODEL = "gemini-3.8-live"
_CLOSED = object()


class GeminiBackend:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        system_prompt: str = SYSTEM_PROMPT,
        tools: list[ToolSpec] = ALL_TOOLS,
    ):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_prompt,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                        for t in tools
                    ]
                )
            ],
            # 降低"说完了"的灵敏度：验证中 AI 常在句中停顿处（如"AI，帮我记一下，……"）抢话
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    silence_duration_ms=800,
                )
            ),
        )
        self._cm = None
        self._session = None
        self._receiver: asyncio.Task | None = None
        self._out: asyncio.Queue = asyncio.Queue()

    async def connect(self) -> None:
        self._cm = self._client.aio.live.connect(model=self._model, config=self._config)
        self._session = await self._cm.__aenter__()
        self._receiver = asyncio.create_task(self._receive())

    async def send_audio(self, frame: AudioFrame) -> None:
        await self._session.send_realtime_input(
            audio=types.Blob(data=frame.pcm16, mime_type=f"audio/pcm;rate={SAMPLE_RATE}")
        )

    async def send_speaker_label(self, label: SpeakerLabel) -> None:
        await self.send_note(label.text)

    async def send_note(self, text: str) -> None:
        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=False
        )

    async def send_tool_result(self, call_id: str, tool: str, result: dict[str, Any]) -> None:
        await self._session.send_tool_response(
            function_responses=[types.FunctionResponse(id=call_id, name=tool, response=result)]
        )

    async def events(self) -> AsyncIterator[BackendEvent]:
        while (e := await self._out.get()) is not _CLOSED:
            yield e

    async def close(self) -> None:
        if self._receiver:
            self._receiver.cancel()
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass  # 连接本来就断了时，关闭也会报错，忽略
        self._out.put_nowait(_CLOSED)

    async def _receive(self) -> None:
        put = self._out.put_nowait
        try:
            while True:
                async for msg in self._session.receive():
                    if msg.data:
                        put(BackendEvent("ai_audio", audio=msg.data))
                    if msg.tool_call:
                        for fc in msg.tool_call.function_calls or []:
                            put(BackendEvent("tool_call", tool=fc.name, args=dict(fc.args or {}), call_id=fc.id or ""))
                    if msg.go_away:
                        put(BackendEvent("go_away", text=str(msg.go_away.time_left or "")))
                    sc = msg.server_content
                    if sc is None:
                        continue
                    if sc.input_transcription and sc.input_transcription.text:
                        put(BackendEvent("user_text", text=sc.input_transcription.text))
                    if sc.output_transcription and sc.output_transcription.text:
                        put(BackendEvent("ai_text", text=sc.output_transcription.text))
                    if sc.interrupted:
                        put(BackendEvent("interrupted"))
                    if sc.turn_complete:
                        put(BackendEvent("ai_turn_end"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            put(BackendEvent("error", text=f"{type(exc).__name__}: {exc}"))
