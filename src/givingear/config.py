"""用户配置：API 密钥由用户自己提供，从环境变量或本地配置文件读取。"""

from __future__ import annotations

import os
from pathlib import Path


class ConfigError(Exception):
    """配置问题，消息直接展示给用户。"""


def config_file() -> Path:
    return Path(os.environ.get("GIVINGEAR_CONFIG", Path.home() / ".config" / "givingear" / "env"))


def gemini_api_key() -> str:
    if key := os.environ.get("GEMINI_API_KEY", "").strip():
        return key
    path = config_file()
    if path.exists():
        for line in path.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GEMINI_API_KEY" and value.strip():
                return value.strip()
    raise ConfigError(
        "找不到 Gemini API 密钥。\n"
        "请在 Google AI Studio（https://aistudio.google.com/apikey）申请密钥，然后任选一种方式提供：\n"
        "  1. 环境变量：export GEMINI_API_KEY=你的密钥\n"
        f"  2. 配置文件：在 {path} 中写一行 GEMINI_API_KEY=你的密钥"
    )
