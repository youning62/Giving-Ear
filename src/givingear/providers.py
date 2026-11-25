"""模型提供商：Gemini（原生语音）和文字模型（DeepSeek 预设、自定义的 OpenAI 兼容接口）。

非密钥的设置存在配置目录的 settings.json；密钥在同目录的 env 文件里（权限 600）。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .config import ConfigError, config_file


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # "live"：原生语音模型；"chat"：文字模型 + 本地语音
    key_env: str
    note: str = ""
    base_url: str = ""
    model: str = ""


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider("gemini", "Google Gemini（原生语音）", "live", "GEMINI_API_KEY", "延迟最低，但在中国大陆通常无法直接连接"),
        Provider(
            "deepseek", "DeepSeek（文字模型）", "chat", "DEEPSEEK_API_KEY",
            "国内可直接使用。AI 的声音在“AI 的声音”里另选", "https://api.deepseek.com", "deepseek-chat",
        ),
        Provider("custom", "自定义（OpenAI 兼容接口）", "chat", "CUSTOM_API_KEY", "通义、智谱、月之暗面等兼容 OpenAI 接口的模型，填地址和模型名即可"),
    ]
}


@dataclass
class Settings:
    provider: str = "gemini"
    deepseek_model: str = "deepseek-chat"
    custom_base_url: str = ""
    custom_model: str = ""
    zh_voice: str = "Tingting"
    en_voice: str = "Samantha"
    tts_engine: str = "mac"  # mac：本机自带（无情感）；qwen：通义千问云端；cosyvoice：本地 CosyVoice 服务
    qwen_voice: str = "Cherry"
    qwen_model: str = "qwen3-tts-instruct-flash"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    cosyvoice_url: str = "http://127.0.0.1:50000"
    cosyvoice_mode: str = "instruct2"
    cosyvoice_speaker: str = ""
    cosyvoice_rate: str = "24000"

    def endpoint(self, provider_id: str | None = None) -> tuple[str, str]:
        """文字模型的 (接口地址, 模型名)。"""
        pid = provider_id or self.provider
        if pid == "deepseek":
            return PROVIDERS["deepseek"].base_url, self.deepseek_model or PROVIDERS["deepseek"].model
        if pid == "custom":
            if not self.custom_base_url.strip() or not self.custom_model.strip():
                raise ConfigError("自定义模型需要先填写接口地址和模型名")
            return self.custom_base_url.strip(), self.custom_model.strip()
        raise ConfigError(f"{pid} 不是文字模型")


TTS_ENGINES = {
    "mac": "本机自带（免费离线，情感较弱：只调语速、音高、音量）",
    "qwen": "通义千问云端（推荐：有情感，国内直连，手机也能用；需要 DashScope 密钥）",
    "cosyvoice": "本地 CosyVoice（有情感，仅限电脑，需要自己部署服务）",
}
TTS_KEY_ENV = "DASHSCOPE_API_KEY"  # 通义千问语音合成用的密钥（和文字模型的密钥分开存）


def settings_file():
    return config_file().parent / "settings.json"


def load_settings() -> Settings:
    path = settings_file()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return Settings()
    known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__ and isinstance(v, str)}
    s = Settings(**known)
    if s.provider not in PROVIDERS:
        s.provider = "gemini"
    return s


def save_settings(s: Settings) -> None:
    if s.provider not in PROVIDERS:
        raise ConfigError("不支持这个模型提供商")
    for url in (s.custom_base_url, s.qwen_base_url, s.cosyvoice_url):
        if url.strip() and not url.strip().startswith(("http://", "https://")):
            raise ConfigError("接口地址要以 http:// 或 https:// 开头")
    if s.tts_engine not in TTS_ENGINES:
        raise ConfigError("不支持这个语音引擎")
    if s.cosyvoice_mode not in ("instruct", "instruct2"):
        raise ConfigError("CosyVoice 的模式只能是 instruct 或 instruct2")
    if not s.cosyvoice_rate.isdigit() or not 8000 <= int(s.cosyvoice_rate) <= 48000:
        raise ConfigError("CosyVoice 的采样率要在 8000 到 48000 之间")
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2))
    os.replace(tmp, path)
