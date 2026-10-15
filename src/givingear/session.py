"""会话协调器：把音频源、语音活动检测、说话人识别、语音模型后端串起来，并对外发出事件。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .audio import SAMPLE_RATE, AudioSource
from .backend import BackendEvent, SpeakerLabel, VoiceBackend
from .events import EventBus, event
from .names import Person, find_misheard_names
from .speakers import EmbeddingExtractor, Match, SessionProfiles
from .transcribe import Transcriber
from .vad import SpeechEnd, SpeechStart, Vad

GUESS_AFTER_S = 1.0  # 开口多久后给出初判
HINT_COOLDOWN_S = 60.0  # 同一个误听在这段时间内只提示一次
MIN_GUESS_S = 0.5  # 太短的音频算不出可靠的声纹


class AudioSink(Protocol):
    """播放 AI 语音的地方（扬声器）。"""

    def play(self, pcm16: bytes, rate: int) -> None: ...
    def stop_playback(self) -> None: ...


@dataclass
class _Utterance:
    id: int
    start: float
    guessed: bool = False
    match: Match | None = None


class Session:
    def __init__(
        self,
        backend: VoiceBackend,
        bus: EventBus | None = None,
        extractor: EmbeddingExtractor | None = None,
        profiles: SessionProfiles | None = None,
        guess_after: float = GUESS_AFTER_S,
        sink: AudioSink | None = None,
        transcriber: Transcriber | None = None,
    ):
        self.backend = backend
        self.sink = sink
        self.transcriber = transcriber or Transcriber()
        self._transcribing: set[asyncio.Task] = set()
        self.bus = bus or EventBus()
        self.extractor = extractor or EmbeddingExtractor()
        self.profiles = profiles or SessionProfiles()
        self.guess_after = guess_after
        self._vad = Vad()
        self._utt: _Utterance | None = None
        self._next_utt = 1
        self._ai_speaking = False
        self._recent: Match | None = None  # 最近一句话的说话人，工具调用里"刚才说话的人"指的就是他
        self._roster_sent = ""
        self._hinted: dict[tuple[str, str], float] = {}  # (听到的词, 说话人编号) → 上次提示的音频时间

    async def run(self, source: AudioSource) -> None:
        await self.backend.connect()
        self.bus.publish(event("status", state="listening"))
        replies = asyncio.create_task(self._consume_backend())
        try:
            now = 0.0
            async for frame in source.frames():
                await self.backend.send_audio(frame)
                now = frame.t + len(frame.samples) / SAMPLE_RATE
                await self._on_samples(frame.samples, now)
            # 补一秒静音，让最后一句话也能被判定为说完
            await self._on_samples(np.zeros(SAMPLE_RATE, np.float32), now + 1.0)
            await asyncio.gather(*self._transcribing)
        except Exception as exc:
            self.bus.publish(event("error", message=f"{type(exc).__name__}: {exc}"))
            raise
        finally:
            await self.backend.close()
            replies.cancel()
            self.bus.publish(event("status", state="stopped"))

    async def _consume_backend(self) -> None:
        async for e in self.backend.events():
            try:
                await self._on_backend_event(e)
            except Exception as exc:  # 单条消息处理失败不应让整个回复通道停掉
                self.bus.publish(event("error", message=f"处理模型回复时出错：{type(exc).__name__}: {exc}"))

    async def _on_backend_event(self, e: BackendEvent) -> None:
        match e.kind:
            case "user_text":
                # 模型自带的转写：到达时间不确定，无法可靠对应到说话人，只作备用
                self.bus.publish(event("model_transcript", text=e.text))
            case "ai_audio":
                if not self._ai_speaking:
                    self._ai_speaking = True
                    self.bus.publish(event("ai_speaking"))
                if self.sink:
                    self.sink.play(e.audio, e.rate)
            case "ai_text":
                self.bus.publish(event("ai_text", text=e.text))
            case "ai_turn_end":
                self._ai_speaking = False
                self.bus.publish(event("ai_turn_end"))
            case "interrupted":
                self._ai_speaking = False
                if self.sink:
                    self.sink.stop_playback()
                self.bus.publish(event("ai_interrupted"))
            case "go_away":
                self.bus.publish(event("status", state="session_ending", time_left=e.text))
            case "tool_call":
                result = self._call_tool(e.tool, e.args)
                self.bus.publish(event("tool_call", tool=e.tool, args=e.args, result=result))
                await self.backend.send_tool_result(e.call_id, e.tool, result)
                await self._send_roster_if_changed()
            case "error":
                self.bus.publish(event("error", message=e.text))

    def _call_tool(self, tool: str, args: dict) -> dict:
        match tool:
            case "name_speaker":
                return self._name_speaker(args)
        return {"ok": False, "error": f"没有这个工具：{tool}"}

    def _name_speaker(self, args: dict) -> dict:
        name = str(args.get("name", "")).strip()
        if not name:
            return {"ok": False, "error": "缺少名字"}
        ref = str(args.get("speaker") or "").strip()
        profile = self.profiles.find(ref) if ref else (self._recent.profile if self._recent else None)
        if profile is None:
            who = f"找不到“{ref}”" if ref else "还没有人说过话"
            return {"ok": False, "error": f"{who}，不知道要给谁命名"}
        old = profile.name
        profile.name, profile.named = name, True
        aliases = [str(a).strip() for a in args.get("aliases") or []]
        profile.aliases = list(dict.fromkeys(a for a in aliases if a and a != name))
        self.bus.publish(
            event(
                "profile_renamed",
                speaker_id=profile.id,
                old_name=old,
                name=name,
                aliases=profile.aliases,
                registered=False,
            )
        )
        return {"ok": True, "speaker": old, "name": name, "note": "只在本次对话中有效"}

    async def _on_samples(self, samples: np.ndarray, now: float) -> None:
        for e in self._vad.accept(samples):
            if isinstance(e, SpeechStart):
                self._utt = _Utterance(self._next_utt, e.t)
                self._next_utt += 1
                self.bus.publish(event("speech_start", e.t, utterance_id=self._utt.id))
            else:
                await self._on_speech_end(e)
        utt = self._utt
        if utt and not utt.guessed and now - utt.start >= self.guess_after:
            samples_so_far = self._vad.current_speech()
            if len(samples_so_far) >= MIN_GUESS_S * SAMPLE_RATE:
                await self._guess(utt, samples_so_far, now)

    async def _guess(self, utt: _Utterance, samples: np.ndarray, now: float) -> None:
        emb = await asyncio.to_thread(self.extractor.embed, samples)
        match = self.profiles.identify(emb)
        utt.guessed, utt.match = True, match
        self._recent = match
        p = match.profile
        if match.is_new:
            self.bus.publish(event("profile_created", now, speaker_id=p.id, name=p.name, registered=False))
        self.bus.publish(
            event(
                "speaker_guess",
                now,
                utterance_id=utt.id,
                speaker_id=p.id,
                name=p.name,
                score=round(match.score, 3),
                new=match.is_new,
            )
        )
        await self.backend.send_speaker_label(SpeakerLabel(p.id, p.name, now, utt.start))

    async def _on_speech_end(self, e: SpeechEnd) -> None:
        utt = self._utt or _Utterance(self._next_utt, e.t_start)
        self._utt = None
        if not utt.guessed:  # 不到初判时间就说完了，用整句判断
            await self._guess(utt, e.samples, e.t_end)
        p = utt.match.profile
        emb = await asyncio.to_thread(self.extractor.embed, e.samples)
        score = p.score(emb)
        # 整句话和初判的档案足够像，才用来充实档案，避免把别人的声音混进来
        if score >= self.profiles.threshold:
            self.profiles.add(p, emb)
        speaker_id, name = p.id, p.name
        task = asyncio.create_task(self._transcribe(utt.id, speaker_id, name, e))
        self._transcribing.add(task)
        task.add_done_callback(self._transcribing.discard)
        self.bus.publish(
            event(
                "speech_end",
                e.t_end,
                utterance_id=utt.id,
                speaker_id=speaker_id,
                name=name,
                start=round(e.t_start, 3),
                full_score=round(score, 3),
            )
        )

    def roster_text(self) -> str:
        """房间名单，告诉模型谁是谁、名字可能被听成什么。"""
        parts = []
        for p in self.profiles.profiles:
            if not p.named:
                parts.append(f"{p.name}（还不知道名字）")
                continue
            heard = f"，名字也可能被听成：{'、'.join(p.aliases)}" if p.aliases else ""
            parts.append(f"{p.name}（{p.label}{heard}）")
        return "[房间里的人：" + "；".join(parts) + "]"

    async def _send_roster_if_changed(self) -> None:
        if not any(p.named for p in self.profiles.profiles):
            return
        text = self.roster_text()
        if text != self._roster_sent:
            self._roster_sent = text
            await self.backend.send_note(text)
            self.bus.publish(event("roster", text=text))

    async def _check_names(self, text: str, speaker_name: str, t: float) -> None:
        people = [Person(p.id, p.name, tuple(p.aliases)) for p in self.profiles.profiles if p.named]
        for hint in find_misheard_names(text, people):
            key = (hint.heard, hint.person.speaker_id)
            if t - self._hinted.get(key, -HINT_COOLDOWN_S) < HINT_COOLDOWN_S:
                continue
            self._hinted[key] = t
            note = f"[提示：{speaker_name} 刚才说的“{hint.heard}”可能是指 {hint.person.name}]"
            await self.backend.send_note(note)
            self.bus.publish(
                event(
                    "name_hint",
                    t,
                    heard=hint.heard,
                    speaker_id=hint.person.speaker_id,
                    name=hint.person.name,
                    score=hint.score,
                )
            )

    async def _transcribe(self, utterance_id: int, speaker_id: str, name: str, e: SpeechEnd) -> None:
        text = await asyncio.to_thread(self.transcriber.transcribe, e.samples)
        if text:
            await self._check_names(text, name, e.t_end)
            self.bus.publish(
                event(
                    "transcript",
                    e.t_end,
                    utterance_id=utterance_id,
                    speaker_id=speaker_id,
                    name=name,
                    text=text,
                    start=round(e.t_start, 3),
                )
            )
