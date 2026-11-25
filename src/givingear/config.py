"""用户配置：API 密钥由用户自己提供，从环境变量或本地配置文件读取。"""

from __future__ import annotations

import os
from pathlib import Path


class ConfigError(Exception):
    """配置问题，消息直接展示给用户。"""


def config_file() -> Path:
    return Path(os.environ.get("GIVINGEAR_CONFIG", Path.home() / ".config" / "givingear" / "env"))


def read_key(env_name: str) -> str:
    """读某个提供商的密钥：先看环境变量，再看本地配置文件；没有就返回空字符串。"""
    if key := os.environ.get(env_name, "").strip():
        return key
    for line in _read_lines():
        name, _, value = line.partition("=")
        if name.strip() == env_name and value.strip():
            return value.strip()
    return ""


def gemini_api_key() -> str:
    if key := read_key("GEMINI_API_KEY"):
        return key
    raise ConfigError(
        "找不到 Gemini API 密钥。\n"
        "请在 Google AI Studio（https://aistudio.google.com/apikey）申请密钥，然后任选一种方式提供：\n"
        "  1. 环境变量：export GEMINI_API_KEY=你的密钥\n"
        f"  2. 配置文件：在 {config_file()} 中写一行 GEMINI_API_KEY=你的密钥\n"
        "  3. 或者在网页的“设置”里填写"
    )


def _read_lines() -> list[str]:
    path = config_file()
    return path.read_text().splitlines() if path.exists() else []


def _write_lines(lines: list[str]) -> None:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # 只有自己能读
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, path)


def save_api_key(env_name: str, key: str) -> None:
    key = key.strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        raise ConfigError("这个密钥看起来不对，请从提供商的控制台复制完整的密钥")
    rest = [l for l in _read_lines() if l.partition("=")[0].strip() != env_name]
    _write_lines([*rest, f"{env_name}={key}"])


def delete_api_key(env_name: str) -> None:
    _write_lines([l for l in _read_lines() if l.partition("=")[0].strip() != env_name])


def save_gemini_api_key(key: str) -> None:
    save_api_key("GEMINI_API_KEY", key)


def delete_gemini_api_key() -> None:
    delete_api_key("GEMINI_API_KEY")


def key_status(env_name: str = "GEMINI_API_KEY") -> dict:
    """给界面用：有没有密钥、末 4 位。绝不返回完整密钥。"""
    key = read_key(env_name)
    return {"has_key": bool(key), "hint": "…" + key[-4:] if key else ""}
