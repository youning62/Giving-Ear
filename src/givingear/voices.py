"""按设置创建语音合成引擎。"""

from __future__ import annotations

from .chat_backend import Synthesizer
from .config import ConfigError, read_key
from .providers import TTS_KEY_ENV, Settings
from .tts import MacSayTTS
from .tts_expressive import CosyVoiceTTS, QwenCloudTTS


def make_synthesizer(s: Settings) -> tuple[Synthesizer, bool]:
    """返回 (引擎, 是否支持情绪)。"""
    if s.tts_engine == "qwen":
        key = read_key(TTS_KEY_ENV)
        if not key:
            raise ConfigError("还没有设置通义千问语音合成（DashScope）的密钥。请在设置的“AI 的声音”里填写，或者改用本机自带的声音")
        return QwenCloudTTS(key, s.qwen_voice or "Cherry", s.qwen_model, s.qwen_base_url), True
    if s.tts_engine == "cosyvoice":
        if not s.cosyvoice_url.strip():
            raise ConfigError("请先在设置里填写 CosyVoice 服务的地址")
        return CosyVoiceTTS(s.cosyvoice_url, s.cosyvoice_mode, s.cosyvoice_speaker, int(s.cosyvoice_rate)), True
    return MacSayTTS(s.zh_voice, s.en_voice), True  # 本机声音靠语速/音高/音量表现情绪，效果有限但免费
