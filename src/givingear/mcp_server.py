"""MCP 服务器（givingear mcp）：让 Claude Desktop、Claude Code 等直接调用"耳朵"。

工具和命令行（givingear listen / transcript / who / name / diarize）是同一套功能：
后台收音进程在本机分辨说话人、转写，这里只是转一道手。所有数据都留在本机。
"""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, listen
from .transcript import format_text

INSTRUCTIONS = (
    "GivingEar 是一只本地的耳朵：用这台 Mac 的麦克风听房间里几个人说话，分辨每句话是谁说的（声纹识别），"
    "在本机转成文字。用 start_listening 开始，之后用 get_transcript 查谁说了什么。"
    "开始前提醒用户告诉在场的人正在记录。对话记录只在内存里，stop_listening 后就没有了（除非指定 save_path）。"
    "“说话人 1”这类名字表示还不知道这个人是谁；知道名字后用 name_speaker 命名，之前的记录也会改成新名字。"
)

server = MCPServer("givingear", instructions=INSTRUCTIONS, version=__version__)
_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def _err(exc: Exception) -> str:
    return f"出错：{exc}"


@server.tool(annotations=_LOCAL)
async def start_listening(max_minutes: float = listen.DEFAULT_MAX_MINUTES) -> str:
    """Start listening to the room through this Mac's microphone in the background, identifying who is speaking
    (speaker identification / diarization) and transcribing locally. 开始用麦克风在后台收音、分辨说话人、本地转写。
    Already listening is fine (returns the current status). First start downloads ~400 MB of local models."""
    try:
        s = await asyncio.to_thread(listen.start, None, max_minutes)
    except listen.ListenError as exc:
        return _err(exc)
    if s["already_running"]:
        return f"已经在听了：{s['minutes']} 分钟，{s['speakers']} 个人，{s['utterances']} 句。"
    return f"开始在后台收音，最长 {s['max_minutes']:g} 分钟。请提醒用户告诉在场的人正在记录。"


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False))
async def stop_listening(save_path: str = "") -> str:
    """Stop listening. The in-memory transcript is discarded unless save_path is given (saved as plain text).
    停止收音；不给 save_path 时，内存里的对话记录会丢弃。Get the transcript first if you still need it."""
    try:
        if save_path and await asyncio.to_thread(listen.read_state):
            rows = await asyncio.to_thread(listen.transcript)
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(format_text(rows) + "\n")
        r = await asyncio.to_thread(listen.stop)
    except (listen.ListenError, OSError) as exc:
        return _err(exc)
    if not r["was_running"]:
        return "本来就没有在听。"
    return f"已停止：{r['minutes']} 分钟、{r['utterances']} 句。" + (f"已保存到 {save_path}。" if save_path else "对话记录已丢弃。")


@server.tool(annotations=_READ)
async def listening_status() -> str:
    """Whether GivingEar is listening, for how long, and how many speakers/utterances so far. 是否在听。"""
    try:
        s = await asyncio.to_thread(listen.status)
    except listen.ListenError as exc:
        return _err(exc)
    if s["state"] == "not_running":
        return "没有在听。"
    return (f"状态：{s['state']}，{s['minutes']} 分钟（最长 {s['max_minutes']:g}），{s['speakers']} 个人，{s['utterances']} 句"
            + (f"，账号 {s['account']}" if s["account"] else "") + (f"。错误：{s['error']}" if s["error"] else "。"))


@server.tool(annotations=_READ)
async def get_transcript(since_minutes: float | None = None, speaker: str = "", last: int | None = None) -> str:
    """Who said what: the speaker-labeled transcript of the room, one line per turn: "[HH:MM:SS] Name: text".
    谁说了什么。Filter by recent minutes, by one speaker (name or label like "说话人 2"), or the last N turns.
    Transcripts come from local speech recognition and may contain misheard words, especially names."""
    try:
        rows = await asyncio.to_thread(listen.transcript, since_minutes, speaker or None, last)
    except listen.ListenError as exc:
        return _err(exc)
    return format_text(rows) or "（还没有对话记录）"


@server.tool(annotations=_READ)
async def list_speakers() -> str:
    """People heard so far in the room: name, label, number of turns, seconds spoken, whether enrolled (voiceprint
    saved in the user's account). 房间里有哪些人。"""
    try:
        rows = await asyncio.to_thread(listen.speakers)
    except listen.ListenError as exc:
        return _err(exc)
    if not rows:
        return "还没有人说话。"
    return "\n".join(f"{r['name']}（{r['speaker_id']}）：{r['utterances']} 句，{r['seconds']} 秒"
                     + ("，已登记" if r["registered"] else "") for r in rows)


@server.tool(annotations=_LOCAL)
async def name_speaker(speaker: str, name: str, aliases: list[str] | None = None) -> str:
    """Give a speaker a name for this listening session, e.g. speaker="说话人 2", name="Jennifer". Earlier lines
    are relabeled too. aliases: other spellings / transliterations / likely mishearings (珍妮弗, Jenny).
    给某个人命名（只在这次收音中有效）。Call when someone introduces themselves or the user tells you who is who."""
    try:
        r = await asyncio.to_thread(listen.name, speaker, name, aliases or [])
    except listen.ListenError as exc:
        return _err(exc)
    return f"{speaker} 现在叫 {name}。" if r.get("ok") else _err(Exception(r.get("error", "失败")))


@server.tool(annotations=_LOCAL)
async def register_speaker(speaker: str, name: str = "") -> str:
    """Permanently enroll a speaker's voiceprint in the user's GivingEar account so they are recognized by name next
    time. Only when the person explicitly asks to be remembered. Works only if the user started listening with
    `givingear listen start --account NAME` in their own terminal (password needed). 永久登记声纹。"""
    try:
        r = await asyncio.to_thread(listen.register, speaker, name)
    except listen.ListenError as exc:
        return _err(exc)
    if r.get("need_more_speech"):
        return f"这个人说的话还不够（{r['have_s']} / {r['need_s']} 秒），请他再随便说几句后再试。"
    return r.get("note", "已登记。") if r.get("ok") else _err(Exception(r.get("error", "失败")))


@server.tool(annotations=_READ)
async def diarize_file(path: str, names: dict[str, str] | None = None) -> str:
    """Transcribe an audio file (wav/mp3/m4a…) with speaker labels, fully on this machine: "[MM:SS] Name: text".
    给录音文件按说话人转写。names optionally maps labels to names, e.g. {"说话人 1": "小王"}."""
    from .diarize import diarize

    try:
        result = await diarize(path, names or {})
    except (FileNotFoundError, RuntimeError) as exc:
        return _err(exc)
    return format_text(result["lines"]) or "（没有检测到说话）"


def main() -> None:
    server.run("stdio")
