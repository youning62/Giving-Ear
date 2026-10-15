"""模型文件：第一次使用时下载到本地缓存目录。"""

from __future__ import annotations

import os
import tarfile
import urllib.request
from pathlib import Path

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

VAD_MODEL = ("silero_vad.onnx", f"{_RELEASES}/asr-models/silero_vad.onnx")
SPEAKER_MODEL = (
    "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
    f"{_RELEASES}/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
)

SENSE_VOICE_MODEL = (
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
    f"{_RELEASES}/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2",
)


def models_dir() -> Path:
    return Path(os.environ.get("GIVINGEAR_MODELS_DIR", Path.home() / ".cache" / "givingear" / "models"))


def ensure(model: tuple[str, str]) -> Path:
    name, url = model
    path = models_dir() / name
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"正在下载模型 {name} …")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(path)
    return path



def ensure_dir(model: tuple[str, str]) -> Path:
    """压缩包形式的模型（一个目录），下载后解压。"""
    name, url = model
    path = models_dir() / name
    if not path.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"正在下载模型 {name} …")
        archive = path.with_suffix(".tar.bz2.part")
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(path.parent, filter="data")
        archive.unlink()
    return path
