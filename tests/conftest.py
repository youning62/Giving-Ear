"""测试语料：用 macOS 自带的语音合成生成多人对话。真人语料（票 02）到位后再补充。"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from givingear.audio import SAMPLE_RATE, read_wav

# 三个声纹区分度好的合成声音（Eddy 和 Reed 的声纹几乎相同，不能同时使用）
VOICES = {
    "A": "Eddy (Chinese (China mainland))",
    "B": "Tingting",
    "C": "Sandy (Chinese (China mainland))",
}

CONVERSATION = [
    ("A", "小李，你周末打算干嘛？我听说天气会很好。"),
    ("B", "我想去爬山，你要不要一起来？"),
    ("A", "好啊，那我们周六早上七点出发吧。"),
    ("B", "没问题，我会带上水和一些吃的。"),
    ("C", "你们在说什么呢？这个 plan 也算我一个。"),
    ("A", "当然可以，人多更热闹，周六见。"),
]
GAP_S = 1.2


@dataclass(frozen=True)
class Turn:
    speaker: str
    start: float
    end: float


@dataclass(frozen=True)
class Corpus:
    path: Path
    turns: list[Turn]


def _synth(voice: str, text: str, out: Path) -> np.ndarray:
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1", str(aiff), str(out)], check=True
    )
    return read_wav(out)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


@pytest.fixture(scope="session")
def conversation(tmp_path_factory) -> Corpus:
    if not shutil.which("say"):
        pytest.skip("需要 macOS 的 say 命令生成测试语料")
    d = tmp_path_factory.mktemp("corpus")
    pieces, turns, t = [np.zeros(SAMPLE_RATE, np.float32)], [], 1.0
    for i, (spk, text) in enumerate(CONVERSATION):
        x = _synth(VOICES[spk], text, d / f"turn{i}.wav")
        turns.append(Turn(spk, t, t + len(x) / SAMPLE_RATE))
        t += len(x) / SAMPLE_RATE + GAP_S
        pieces += [x, np.zeros(int(SAMPLE_RATE * GAP_S), np.float32)]
    path = d / "conversation.wav"
    _write_wav(path, np.concatenate(pieces))
    return Corpus(path, turns)



