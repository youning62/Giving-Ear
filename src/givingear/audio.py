"""音频源：核心只认统一格式的音频帧（16 kHz、单声道、float32），不关心音频从哪里来。"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Protocol

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


@dataclass(frozen=True)
class AudioFrame:
    samples: np.ndarray  # float32，取值 [-1, 1]
    t: float  # 这一帧在音频流中的起始时间（秒）

    @property
    def pcm16(self) -> bytes:
        return (np.clip(self.samples, -1, 1) * 32767).astype("<i2").tobytes()


class AudioSource(Protocol):
    def frames(self) -> AsyncIterator[AudioFrame]: ...


def read_wav(path: str | Path) -> np.ndarray:
    """读取任意 WAV，返回 16 kHz 单声道 float32。格式不对时用 macOS 自带的 afconvert 转换。"""
    path = Path(path)
    with wave.open(str(path)) as w:
        ok = w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
        if ok:
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    # 转换结果只是输入文件的另一种格式，用完即删
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "converted.wav"
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1", str(path), str(out)], check=True
        )
        return read_wav(out)


class WavFileSource:
    """把 WAV 文件当作麦克风。realtime=True 时按真实速度输出，否则尽快输出（用于测试）。"""

    def __init__(self, path: str | Path, realtime: bool = False):
        self.samples = read_wav(path)
        self.realtime = realtime

    async def frames(self) -> AsyncIterator[AudioFrame]:
        loop = asyncio.get_running_loop()
        start = loop.time()
        for n in range(0, len(self.samples), FRAME_SAMPLES):
            chunk = self.samples[n : n + FRAME_SAMPLES]
            if len(chunk) < FRAME_SAMPLES:
                chunk = np.pad(chunk, (0, FRAME_SAMPLES - len(chunk)))
            t = n / SAMPLE_RATE
            if self.realtime:
                await asyncio.sleep(max(0.0, start + t - loop.time()))
            else:
                await asyncio.sleep(0)  # 让出事件循环，让订阅者和后端有机会处理
            yield AudioFrame(chunk, t)


class StreamResampler:
    """连续音频流的重采样：保留上一块的尾巴，块与块之间没有接缝杂音。"""

    _TAPS = 63

    def __init__(self, from_rate: int, to_rate: int):
        self.from_rate, self.to_rate = from_rate, to_rate
        self._step = from_rate / to_rate  # 每个输出样本前进多少个输入样本
        if to_rate < from_rate:
            cutoff = 0.45 * to_rate / from_rate
            n = np.arange(self._TAPS) - (self._TAPS - 1) / 2
            h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(self._TAPS)
            self._h = (h / h.sum()).astype(np.float32)
        else:
            self._h = None
        self._history = np.zeros(self._TAPS - 1 if self._h is not None else 0, np.float32)
        self._carry = np.zeros(0, np.float32)  # 已滤波、还没用完的输入
        self._pos = 0.0  # 下一个输出样本在 _carry 中的位置

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.from_rate == self.to_rate:
            return x.astype(np.float32)
        if self._h is not None:
            joined = np.concatenate([self._history, x.astype(np.float32)])
            self._history = joined[-(self._TAPS - 1) :]
            x = np.convolve(joined, self._h, mode="valid")
        buf = np.concatenate([self._carry, x])
        positions = np.arange(self._pos, len(buf) - 1, self._step)
        out = np.interp(positions, np.arange(len(buf)), buf).astype(np.float32)
        next_pos = self._pos + len(positions) * self._step
        keep = min(int(next_pos), len(buf))
        self._carry, self._pos = buf[keep:], next_pos - keep
        return out
