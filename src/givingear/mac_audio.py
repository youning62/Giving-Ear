"""macOS 麦克风与扬声器（AVAudioEngine）。

麦克风和播放共用同一个音频引擎，这样系统回声消除才能拿到"正在播放什么"作为参考。
来自验证（票 04）的要点：
- 必须先初始化输出端和混音器，再开启语音处理（回声消除），否则引擎启动失败（-10875）。
- 开启语音处理后输入是 48 kHz、9 个内容相同的声道，取第一个声道即可。
- 已知问题：开启语音处理时，扬声器播放期间人声也会被压掉（无法边播边打断），待后续改进。
"""

from __future__ import annotations

import asyncio
import threading
from typing import AsyncIterator

import AVFoundation as AVF
import numpy as np

from .audio import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame, StreamResampler
from .config import ConfigError

PLAYBACK_RATE = 24000


def ensure_mic_permission() -> None:
    status = AVF.AVCaptureDevice.authorizationStatusForMediaType_(AVF.AVMediaTypeAudio)
    if status == 0:  # 还没问过，弹出系统授权窗口
        done, granted = threading.Event(), []
        AVF.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVF.AVMediaTypeAudio, lambda ok: (granted.append(bool(ok)), done.set())
        )
        done.wait(120)
        status = 3 if granted and granted[0] else 2
    if status != 3:
        raise ConfigError(
            "没有麦克风权限。\n"
            "请打开 系统设置 → 隐私与安全性 → 麦克风，允许你正在使用的终端应用（例如“终端”）访问麦克风，然后重新运行。"
        )


class MacAudio:
    def __init__(self, capture: bool = True, echo_cancellation: bool = True):
        self._capture = capture
        self._engine = AVF.AVAudioEngine.alloc().init()
        self._engine.outputNode()
        self._engine.mainMixerNode()
        self._input = self._engine.inputNode() if capture else None
        if capture and echo_cancellation:
            ok, err = self._input.setVoiceProcessingEnabled_error_(True, None)
            if not ok:
                raise RuntimeError(f"无法开启系统回声消除：{err}")
        self._play_fmt = AVF.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(PLAYBACK_RATE, 1)
        self._player = AVF.AVAudioPlayerNode.alloc().init()
        self._engine.attachNode_(self._player)
        self._engine.connect_to_format_(self._player, self._engine.mainMixerNode(), self._play_fmt)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        ok, err = self._engine.startAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"无法启动音频设备：{err}")
        self._player.play()
        self._started = True

    async def frames(self) -> AsyncIterator[AudioFrame]:
        """麦克风音频，按 20 ms 一帧输出。"""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[np.ndarray] = asyncio.Queue()
        fmt = self._input.outputFormatForBus_(0)
        resampler = StreamResampler(int(fmt.sampleRate()), SAMPLE_RATE)

        def tap(buffer, when):  # 在音频线程上调用
            n = buffer.frameLength()
            x = np.frombuffer(buffer.floatChannelData()[0].as_buffer(n), np.float32).copy()
            loop.call_soon_threadsafe(q.put_nowait, resampler.process(x))

        self._input.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, tap)
        self.start()
        pending, sent = np.zeros(0, np.float32), 0
        try:
            while True:
                pending = np.concatenate([pending, await q.get()])
                while len(pending) >= FRAME_SAMPLES:
                    yield AudioFrame(pending[:FRAME_SAMPLES], sent / SAMPLE_RATE)
                    pending, sent = pending[FRAME_SAMPLES:], sent + FRAME_SAMPLES
        finally:
            self._input.removeTapOnBus_(0)

    def play(self, pcm16: bytes, rate: int = PLAYBACK_RATE) -> None:
        x = np.frombuffer(pcm16, "<i2").astype(np.float32) / 32768
        if rate != PLAYBACK_RATE:
            x = StreamResampler(rate, PLAYBACK_RATE).process(x)
        if len(x) == 0:
            return
        self.start()
        buf = AVF.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(self._play_fmt, len(x))
        np.ndarray((len(x),), np.float32, buffer=buf.floatChannelData()[0].as_buffer(len(x)))[:] = x
        buf.setFrameLength_(len(x))
        self._player.scheduleBuffer_completionHandler_(buf, None)

    def stop_playback(self) -> None:
        """立刻停止播放并丢弃还没播的部分。"""
        self._player.stop()
        self._player.play()

    def close(self) -> None:
        self._player.stop()
        self._engine.stop()
