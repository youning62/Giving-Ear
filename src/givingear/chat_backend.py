"""文字模型后端：每句话本地转成文字（带说话人名字）送给文字模型，回复逐句合成语音播放。

和 Gemini Live 的区别：Gemini 直接听音频、自己判断什么时候说话；文字模型只看文字，
所以"该不该说话"靠系统提示里的约定——不是对 AI 说的话，就只回复 [沉默]，这样的回复不播放。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Protocol

from .audio import AudioFrame
from .backend import BackendEvent, SpeakerLabel
from .emotions import parse_emotion
from .tools import ToolSpec

SILENCE = "沉默"
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;\n])|(?<=[.])(?=\s)")
_CLOSED = object()


class ChatClient(Protocol):
    """文字模型接口。流式返回：("text", 文字片段)，最后可能有若干 ("tool_call", id, 名字, 参数字典)。"""

    def chat(self, messages: list[dict], tools: list[ToolSpec]) -> AsyncIterator[tuple]: ...


class Synthesizer(Protocol):
    async def synth(self, text: str, emotion: str | None = None) -> tuple[bytes, int]:
        """返回 (16 位单声道 PCM, 采样率)。emotion 是 emotions.py 词表里的情绪，不支持情绪的引擎可以忽略。"""
        ...


def split_sentences(buf: str) -> tuple[list[str], str]:
    """把已经收到的文字切成"完整的句子"和"还没写完的尾巴"。"""
    parts = [p for p in _SENTENCE_END.split(buf) if p]
    if parts and not re.search(r"[。！？!?；;\n]\s*$|\.\s+$", buf):
        return parts[:-1], parts[-1]
    return parts, ""


class TextChatBackend:
    def __init__(
        self,
        client: ChatClient,
        synth: Synthesizer,
        system_prompt: str,
        tools: list[ToolSpec] | None = None,
        reply_delay: float = 0.6,
        max_history: int = 40,
    ):
        self._client, self._synth = client, synth
        self._system = system_prompt
        self._tools = tools or []
        self._reply_delay = reply_delay
        self._max_history = max_history
        self._history: list[dict] = []
        self._pending_tools: set[str] = set()
        self._deferred: list[dict] = []  # 等工具结果期间收到的背景说明
        self._out: asyncio.Queue = asyncio.Queue()
        self._timer: asyncio.TimerHandle | None = None
        self._gen: asyncio.Task | None = None
        self._playing_until = 0.0  # 已经发出去的语音大概播到什么时候（用来判断"被打断"）

    # ---- VoiceBackend 接口 ----
    async def connect(self) -> None:
        pass

    async def send_audio(self, frame: AudioFrame) -> None:
        pass  # 文字模型不听音频，会话用本地转写

    async def send_speaker_label(self, label: SpeakerLabel) -> None:
        pass  # 说话人名字随转写一起送

    async def send_note(self, text: str) -> None:
        self._add({"role": "user", "content": text})

    async def send_transcript(self, speaker_id: str, name: str, text: str, t: float) -> None:
        self._add({"role": "user", "content": f"[{name}]：{text}"})
        self._schedule(self._reply_delay)  # 同一个人停顿后接着说、或别人马上接话，只回复一次

    async def interrupt(self) -> None:
        """有人开口了：取消还没说的回复，停止播放。"""
        self._cancel_timer()
        was_generating = self._gen is not None and not self._gen.done()
        if was_generating:
            self._gen.cancel()
            await asyncio.gather(self._gen, return_exceptions=True)
        loop = asyncio.get_running_loop()
        if was_generating or loop.time() < self._playing_until:
            self._playing_until = 0.0
            self._out.put_nowait(BackendEvent("interrupted"))

    async def send_tool_result(self, call_id: str, tool: str, result: dict[str, Any]) -> None:
        self._history.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)})
        self._pending_tools.discard(call_id)
        if not self._pending_tools:
            for m in self._deferred:
                self._history.append(m)
            self._deferred.clear()
            self._schedule(0)  # 模型接着工具结果继续

    async def events(self) -> AsyncIterator[BackendEvent]:
        while (e := await self._out.get()) is not _CLOSED:
            yield e

    async def close(self) -> None:
        self._cancel_timer()
        if self._gen:
            self._gen.cancel()
            await asyncio.gather(self._gen, return_exceptions=True)
        self._out.put_nowait(_CLOSED)

    # ---- 内部 ----
    def _add(self, message: dict) -> None:
        (self._deferred if self._pending_tools else self._history).append(message)

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule(self, delay: float) -> None:
        self._cancel_timer()
        self._timer = asyncio.get_running_loop().call_later(delay, self._start)

    def _start(self) -> None:
        self._timer = None
        if self._gen and not self._gen.done():
            return  # 正在回复；新内容已经在历史里，这一轮结束后不会重复触发，下一句话再触发
        self._gen = asyncio.create_task(self._generate())

    def _context(self) -> list[dict]:
        h = self._history
        if len(h) > self._max_history:
            h = h[-self._max_history :]
            while h and h[0]["role"] != "user":  # 不能从"工具结果"或半截工具调用开始
                h = h[1:]
            self._history = h
        return [{"role": "system", "content": self._system}, *h]

    async def _generate(self) -> None:
        put = self._out.put_nowait
        full, emitted, decided, silent, spoke = "", 0, False, False, False
        calls: list[tuple] = []
        sentences: asyncio.Queue = asyncio.Queue()
        speaker = asyncio.create_task(self._speak(sentences))
        try:
            async for item in self._client.chat(self._context(), self._tools):
                if item[0] == "tool_call":
                    calls.append(item[1:])
                    continue
                full += item[1]
                if not decided:
                    head = full.lstrip()
                    if not head or (head[0] == "[" and "]" not in head):
                        continue  # 还没看出是不是"[沉默]"，先等
                    decided = True
                    silent = head[0] == "[" and SILENCE in head[: head.index("]") + 1]
                if silent:
                    continue
                done, _ = split_sentences(full[emitted:])
                for sent in done:
                    emitted += len(sent)
                    sentences.put_nowait(sent)
                    spoke = True
            if not silent and full[emitted:].strip():
                sentences.put_nowait(full[emitted:])
                spoke = True
            sentences.put_nowait(None)
            await speaker
            msg: dict = {"role": "assistant", "content": f"[{SILENCE}]" if silent else (full or None)}
            if calls:
                msg["tool_calls"] = [
                    {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                    for cid, name, args in calls
                ]
                self._history.append(msg)
                self._pending_tools = {cid for cid, _, _ in calls}
                for cid, name, args in calls:
                    put(BackendEvent("tool_call", tool=name, args=args, call_id=cid))
                return
            self._history.append(msg)
            if spoke:
                put(BackendEvent("ai_turn_end"))
        except asyncio.CancelledError:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            raise
        except Exception as exc:
            speaker.cancel()
            await asyncio.gather(speaker, return_exceptions=True)
            put(BackendEvent("error", text=f"{type(exc).__name__}: {exc}"))

    async def _speak(self, sentences: asyncio.Queue) -> None:
        loop = asyncio.get_running_loop()
        emotion = None  # 这一轮最近用的情绪；句子没标签时沿用
        while (s := await sentences.get()) is not None:
            tagged, s = parse_emotion(s)
            emotion = tagged or emotion
            if not s.strip():
                continue
            self._out.put_nowait(BackendEvent("ai_text", text=s))
            pcm, rate = await self._synth.synth(s, emotion)
            if pcm:
                self._out.put_nowait(BackendEvent("ai_audio", audio=pcm, rate=rate))
                now = loop.time()
                self._playing_until = max(self._playing_until, now) + len(pcm) / 2 / rate
