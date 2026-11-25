"""语音合成。接口只有一个 synth(text)，以后可以换成本地神经网络合成或云端合成。"""

from __future__ import annotations

import asyncio
import re
import tempfile
import wave
from pathlib import Path

PLAYBACK_RATE = 24000
_CJK = re.compile(r"[一-鿿]")

# macOS 自带的声音：中文用婷婷，其他用 Samantha
DEFAULT_ZH_VOICE = "Tingting"
DEFAULT_EN_VOICE = "Samantha"


class SynthError(Exception):
    pass


# 情绪 → (音高变化, 语速变化, 音量)。macOS 的 say 支持在文字前面加 [[pbas +10]] 之类的嵌入命令；
# 实测（Tingting）：开心约抬高 17% 的音高并加快，难过约压低 46% 并放慢、变轻。音量 None 表示不改。
PROSODY: dict[str, tuple[int, int, float | None]] = {
    "平静": (0, 0, None),
    "开心": (10, 40, None),
    "兴奋": (14, 55, None),
    "温柔": (2, -30, 0.8),
    "关切": (-2, -25, 0.85),
    "难过": (-12, -45, 0.65),
    "惊讶": (14, 10, None),
    "生气": (-2, 30, None),
    "俏皮": (7, 25, None),
}


def prosody_prefix(emotion: str | None) -> str:
    pitch, rate, volume = PROSODY.get(emotion or "平静", PROSODY["平静"])
    return (f"[[pbas {pitch:+d}]]" if pitch else "") + (f"[[rate {rate:+d}]]" if rate else "") + (f"[[volm {volume}]]" if volume else "")


def clean_for_say(text: str) -> str:
    """去掉文字里可能带的 say 嵌入命令，免得模型的输出被当成命令执行。"""
    return text.replace("[[", "").replace("]]", "").strip()


class MacSayTTS:
    """macOS 自带的 say：离线、免费，声音偏机械。合成用的临时文件放在临时目录里，用完即删。"""

    def __init__(self, zh_voice: str = DEFAULT_ZH_VOICE, en_voice: str = DEFAULT_EN_VOICE):
        self.zh_voice, self.en_voice = zh_voice, en_voice

    def voice_for(self, text: str) -> str:
        return self.zh_voice if _CJK.search(text) else self.en_voice

    async def synth(self, text: str, emotion: str | None = None) -> tuple[bytes, int]:
        text = clean_for_say(text)
        if not text:
            return b"", PLAYBACK_RATE
        with tempfile.TemporaryDirectory() as tmp:
            aiff, wav = Path(tmp) / "s.aiff", Path(tmp) / "s.wav"
            await _run("say", "-v", self.voice_for(text), "-o", str(aiff), "--", prosody_prefix(emotion) + text)
            await _run("afconvert", "-f", "WAVE", "-d", f"LEI16@{PLAYBACK_RATE}", "-c", "1", str(aiff), str(wav))
            with wave.open(str(wav)) as w:
                return w.readframes(w.getnframes()), w.getframerate()


async def _run(*cmd: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise SynthError(f"找不到 {cmd[0]}：语音合成目前只支持 macOS 自带的声音") from None
    _, err = await proc.communicate()
    if proc.returncode:
        raise SynthError(f"{cmd[0]} 失败：{err.decode(errors='replace').strip()}")
