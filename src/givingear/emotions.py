"""AI 说话的情绪：文字模型给每句话标一个情绪，语音合成按它的语气朗读。"""

from __future__ import annotations

import re

# 情绪 → 语气指令（自然语言，给支持指令的语音合成用）
EMOTIONS: dict[str, str] = {
    "平静": "用平静自然的语气，语速适中",
    "开心": "用开心愉快的语气，语速稍快，声音明亮",
    "兴奋": "用兴奋激动的语气，语速较快，充满活力",
    "温柔": "用温柔亲切的语气，语速稍慢，声音柔和",
    "关切": "用关心体贴的语气，语速稍慢，声音温暖",
    "难过": "用低落难过的语气，语速较慢，声音轻柔",
    "惊讶": "用惊讶的语气，语调上扬",
    "生气": "用生气不满的语气，语速较快，语气有力",
    "俏皮": "用俏皮调侃的语气，轻松活泼",
}

_TAG = re.compile(r"^\s*[\[【(（]\s*(" + "|".join(EMOTIONS) + r")\s*[\]】)）]\s*")


def parse_emotion(sentence: str) -> tuple[str | None, str]:
    """把句首的情绪标签剥掉：返回 (情绪, 干净的文字)。词表以外的方括号（例如 [小王]）原样保留。"""
    m = _TAG.match(sentence)
    return (m.group(1), sentence[m.end() :]) if m else (None, sentence)


def instruction(emotion: str | None) -> str:
    return EMOTIONS.get(emotion or "平静", EMOTIONS["平静"])
