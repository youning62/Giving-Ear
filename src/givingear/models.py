"""模型文件：第一次使用时下载到本地缓存目录。"""

from __future__ import annotations

import os
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

from .config import ConfigError

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


class ModelDownloadError(ConfigError):
    """模型下载失败，消息可以直接显示给用户。"""


def download_url(url: str) -> str:
    """国内访问 GitHub 常常很慢或不通：设置 GIVINGEAR_DOWNLOAD_MIRROR 后，地址会加上这个前缀。"""
    mirror = os.environ.get("GIVINGEAR_DOWNLOAD_MIRROR", "").strip()
    return f"{mirror.rstrip('/')}/{url}" if mirror else url


def _download(name: str, url: str, dest: Path) -> None:
    real = download_url(url)
    print(f"正在下载模型 {name} …")
    try:
        urllib.request.urlretrieve(real, dest)
    except (urllib.error.URLError, OSError) as exc:
        dest.unlink(missing_ok=True)  # 不留下半截文件
        raise ModelDownloadError(
            f"下载模型 {name} 失败：{exc}\n"
            f"  下载地址：{real}\n"
            f"  在国内网络下，可以设置环境变量 GIVINGEAR_DOWNLOAD_MIRROR=镜像前缀 后重试；\n"
            f"  也可以用浏览器或下载工具取得这个文件，放到 {models_dir()} 目录下，程序就会直接使用。"
        ) from exc


def ensure(model: tuple[str, str]) -> Path:
    name, url = model
    path = models_dir() / name
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        _download(name, url, tmp)
        tmp.rename(path)
    return path


def ensure_dir(model: tuple[str, str]) -> Path:
    """压缩包形式的模型（一个目录），下载后解压。"""
    name, url = model
    path = models_dir() / name
    if not path.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        archive = path.with_suffix(".tar.bz2.part")
        _download(name, url, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(path.parent, filter="data")
        archive.unlink()
    return path
