"""名字纠错：语音识别常把人名听错（如 Jennifer → 杰夫），按读音把转写里的词和房间名单比对，找出可能指的是谁。

做法：中文转成拼音，英文按拼写，再统一映射到一套粗略的读音字母（zh/j 不分、n/ng 不分……），
最后用编辑距离算相似度。这是启发式方法，只用来给 AI 提示，不直接改任何东西。
"""

from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass

from pypinyin import lazy_pinyin

with warnings.catch_warnings():  # jieba 的旧代码在新版 Python 上会报语法警告，不影响使用
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba
    import jieba.posseg

jieba.setLogLevel(logging.WARNING)

MIN_SIMILARITY = 0.65
MIN_SIMILARITY_SHORT = 0.8  # 读音串不超过 4 个字母时，偶然相像的概率高，要求更像

# 读音相近、识别时容易混的声母韵母，归成同一个
_COARSE = [
    ("zh", "j"), ("ch", "q"), ("sh", "x"),
    ("ng", "n"), ("ph", "f"), ("ck", "k"), ("ie", "e"), ("ei", "e"), ("ou", "u"),
    ("v", "w"), ("y", "i"), ("h", ""),
]
_HAN = re.compile(r"[一-鿿]")


def phonetic_key(text: str) -> str:
    """把名字转成粗略的读音串，用来比较"听起来像不像"。"""
    parts = []
    for token in re.findall(r"[一-鿿]|[A-Za-z]+", text):
        if _HAN.match(token):
            parts.append(lazy_pinyin(token)[0])
        else:
            word = re.sub(r"(.)\1+", r"\1", token.lower())  # 叠字母只算一个：jennifer → jenifer
            parts.append(re.sub(r"er$", "e", word))  # 英文结尾的 -er 在中文里常被听成"夫/弗"一类的音
    key = "".join(parts)
    for a, b in _COARSE:
        key = key.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", key)  # 映射后也可能出现叠字母：zhen+ni → jenni → jeni


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_PREFIXES = {"小": "xiao", "老": "lao", "阿": "a"}


def _strip_common_prefix(a: str, b: str) -> tuple[str, str]:
    """"小陈"和"小王"（或"Xiao Wang"）只比较姓：共同的"小"不说明它们像。"""
    for a_, b_ in ((a, b), (b, a)):
        p = a_[:1]
        if len(a_) > 1 and p in _PREFIXES and len(b_) > 1:
            if b_[0] == p:
                rest = b_[1:]
            elif b_.lower().startswith(_PREFIXES[p]):
                rest = b_[len(_PREFIXES[p]) :]
            else:
                continue
            return (a_[1:], rest.strip()) if a_ is a else (rest.strip(), a_[1:])
    return a, b


def similarity(a: str, b: str) -> float:
    a, b = _strip_common_prefix(a, b)
    ka, kb = phonetic_key(a), phonetic_key(b)
    if not ka or not kb:
        return 0.0
    return 1 - _distance(ka, kb) / max(len(ka), len(kb))


def _threshold(a: str, b: str) -> float:
    return MIN_SIMILARITY_SHORT if max(len(phonetic_key(a)), len(phonetic_key(b))) <= 4 else MIN_SIMILARITY


@dataclass(frozen=True)
class Person:
    speaker_id: str
    name: str
    aliases: tuple[str, ...] = ()

    @property
    def spellings(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class NameHint:
    heard: str  # 转写里出现的词
    person: Person
    score: float


RARE_WORD_FREQ = 50  # 词典里出现次数低于这个值的词，可能是听错的名字


def _candidates(text: str) -> list[str]:
    """转写里可能是名字的词：英文单词；分词后被标为人名、或词典里很少见的中文词；“小/老/阿 + 一个字”的称呼。"""
    out = re.findall(r"[A-Za-z]{3,}", text)
    out += re.findall(r"[小老阿][一-鿿]", text)
    for w in jieba.posseg.cut(text):
        if len(w.word) >= 2 and _HAN.match(w.word):
            if w.flag.startswith("nr") or jieba.get_FREQ(w.word, 0) < RARE_WORD_FREQ:
                out.append(w.word)
    return out


def find_misheard_names(text: str, people: list[Person]) -> list[NameHint]:
    """找出转写里"不是已知写法、但读音很像某个已知的人"的词。每个人最多一条，取最像的。"""
    known = {s.lower() for p in people for s in p.spellings}
    best: dict[str, NameHint] = {}
    for cand in _candidates(text):
        c = cand.lower()
        # 片段和某个已知写法互相包含（比如就是正确的名字，或名字的一部分），不算听错
        if any(c in k or k in c for k in known):
            continue
        for p in people:
            score, spelling = max((similarity(cand, s), s) for s in p.spellings)
            if score >= _threshold(cand, spelling) and (p.speaker_id not in best or score > best[p.speaker_id].score):
                best[p.speaker_id] = NameHint(cand, p, round(score, 2))
    return list(best.values())
