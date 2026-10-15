"""语音活动检测：把连续音频切成一句一句，并在说话过程中随时给出这句话已有的音频。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import sherpa_onnx

from . import models
from .audio import SAMPLE_RATE


@dataclass
class SpeechStart:
    t: float  # 这句话的开始时间（音频时间轴）


@dataclass
class SpeechEnd:
    t_start: float
    t_end: float
    samples: np.ndarray


class Vad:
    def __init__(self, threshold: float = 0.5, min_silence: float = 0.5, min_speech: float = 0.25):
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(models.ensure(models.VAD_MODEL))
        config.silero_vad.threshold = threshold
        config.silero_vad.min_silence_duration = min_silence
        config.silero_vad.min_speech_duration = min_speech
        config.sample_rate = SAMPLE_RATE
        self._window = config.silero_vad.window_size
        self._vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        self._pending = np.zeros(0, np.float32)
        self._in_speech = False

    def accept(self, samples: np.ndarray) -> list[SpeechStart | SpeechEnd]:
        """送入一段音频，返回这段音频里发生的开口 / 说完事件。"""
        events: list[SpeechStart | SpeechEnd] = []
        self._pending = np.concatenate([self._pending, samples])
        while len(self._pending) >= self._window:
            self._vad.accept_waveform(self._pending[: self._window])
            self._pending = self._pending[self._window :]
            detected = self._vad.is_speech_detected()
            if detected and not self._in_speech:
                events.append(SpeechStart(self._vad.current_segment.start / SAMPLE_RATE))
            self._in_speech = detected
            while not self._vad.empty():
                seg = self._vad.front
                start = seg.start / SAMPLE_RATE
                events.append(SpeechEnd(start, start + len(seg.samples) / SAMPLE_RATE, np.array(seg.samples, np.float32)))
                self._vad.pop()
        return events

    def current_speech(self) -> np.ndarray:
        """正在说的这句话到目前为止的音频；没人说话时为空。"""
        if not self._in_speech:
            return np.zeros(0, np.float32)
        return np.array(self._vad.current_segment.samples, np.float32)
