"""对话记录：从事件流里收集带说话人的转写，供 AI 助手查询"谁说了什么"。

记录里保存说话人编号，查询时才换成当前的名字：给某人命名或登记之后，他之前说的话也会显示新名字。
只在内存里，不写硬盘。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# 这些事件会改变某个编号对应的名字
_NAME_EVENTS = ("profile_created", "profile_renamed", "registered", "speaker_guess")


@dataclass(frozen=True)
class Line:
    utterance_id: int
    speaker_id: str | None
    text: str
    wall_t: float  # 说完这句话的系统时间
    start: float  # 音频时间轴上的开始、结束（秒）
    end: float


class TranscriptLog:
    def __init__(self, max_lines: int = 20000):
        self.lines: list[Line] = []
        self.names: dict[str, str] = {}
        self.registered: set[str] = set()
        self.started_wall = time.time()
        self._max = max_lines

    def handle(self, e: dict[str, Any]) -> None:
        t = e.get("type")
        sid = e.get("speaker_id")
        if t in _NAME_EVENTS and sid and e.get("name"):
            self.names[sid] = e["name"]
            if t == "registered" or e.get("registered"):
                self.registered.add(sid)
        elif t == "transcript" and e.get("text"):
            self.lines.append(
                Line(e["utterance_id"], sid, e["text"], e.get("wall_t", time.time()), e.get("start", 0.0), e.get("audio_t", 0.0))
            )
            if sid and e.get("name") and sid not in self.names:
                self.names[sid] = e["name"]
            del self.lines[: -self._max]

    def name_of(self, speaker_id: str | None) -> str:
        return self.names.get(speaker_id or "", "？")

    def speakers(self) -> list[dict[str, Any]]:
        """房间里出现过的人，按第一次说话的顺序。"""
        out: dict[str, dict[str, Any]] = {}
        for line in self.lines:
            if not line.speaker_id:
                continue
            s = out.setdefault(
                line.speaker_id,
                {"speaker_id": line.speaker_id, "name": self.name_of(line.speaker_id), "utterances": 0,
                 "seconds": 0.0, "registered": line.speaker_id in self.registered},
            )
            s["utterances"] += 1
            s["seconds"] = round(s["seconds"] + max(0.0, line.end - line.start), 1)
        for sid, name in self.names.items():  # 已经被认出、但还没有转写的人
            out.setdefault(sid, {"speaker_id": sid, "name": name, "utterances": 0, "seconds": 0.0,
                                 "registered": sid in self.registered})
        return list(out.values())

    def query(
        self, since_minutes: float | None = None, speaker: str | None = None, last: int | None = None, clock: str = "wall"
    ) -> list[dict]:
        """clock="wall"：时间是几点几分（实时收音）；clock="audio"：时间是在录音里的第几分几秒（录音文件）。"""
        now = time.time()
        rows = []
        key = "".join((speaker or "").split()).lower()
        for line in self.lines:
            if since_minutes is not None and line.wall_t < now - since_minutes * 60:
                continue
            name = self.name_of(line.speaker_id)
            if key and key not in ("".join(name.split()).lower(), (line.speaker_id or "")):
                continue
            stamp = (f"{int(line.start // 60):02d}:{int(line.start % 60):02d}" if clock == "audio"
                     else time.strftime("%H:%M:%S", time.localtime(line.wall_t)))
            rows.append({"time": stamp, "speaker": name,
                         "speaker_id": line.speaker_id, "text": line.text, "wall_t": round(line.wall_t, 3)})
        return rows[-last:] if last else rows


def format_text(rows: list[dict]) -> str:
    """一行一句，连续同一个人说的话合在一起，方便人和 AI 阅读。"""
    out: list[str] = []
    prev = None
    for r in rows:
        if out and r["speaker_id"] == prev and r["speaker_id"] is not None:
            out[-1] += " " + r["text"]
        else:
            out.append(f"[{r['time']}] {r['speaker']}: {r['text']}")
        prev = r["speaker_id"]
    return "\n".join(out)
