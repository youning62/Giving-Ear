"""01 验证：Gemini Live 说话人标签时序。

用 macOS 自带的语音合成生成一段多人对话，按实时速度送进 Gemini Live。
每句话开口约 1 秒时插入说话人标签，记录事件时间线，检查 AI 的回答是否用上了标签。

用法：uv run 01-gemini-label-timing/label_timing.py --mode realtime|client [--no-labels]
"""

import argparse
import asyncio
import json
import os
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

HERE = Path(__file__).parent
OUT = HERE / "out"
MODEL = "gemini-3.8-live"
RATE = 16000
CHUNK_MS = 20
LABEL_DELAY_S = 1.0  # 可用 --delay 覆盖

VOICES = {
    "小王": "Eddy (Chinese (China mainland))",
    "小李": "Tingting",
    "Leo": "Reed (Chinese (China mainland))",
}

# (说话人, 内容, 说完后留的静音秒数)；对 AI 说的话后面留足时间让它回答
SCRIPT = [
    ("小王", "小李，你周末打算干嘛？", 1.5),
    ("小李", "我想去爬山，你要一起吗？", 1.5),
    ("小王", "好啊，那我们周六早上七点出发。", 2.0),
    ("Leo", "AI，帮我记一下，小王和小李周六要去爬山。", 9.0),
    ("小李", "Hey AI, what time did 小王 say we would leave?", 9.0),
    ("小王", "AI，你知道我是谁吗？", 9.0),
    ("Leo", "那刚才是谁提议去爬山的？", 10.0),
]

SYSTEM = (
    "你在一个多人房间里，通过一个麦克风同时听几个人说话。"
    "每当有人开口，系统会插入一条形如“[现在说话的是：名字]”的文字，告诉你此刻说话的人是谁。"
    "这些标签不是用户说的话，不要复述或回应标签本身。"
    "回答时要利用标签：知道是谁在问你，记住谁说过什么，必要时称呼对方的名字。"
    "别人互相交谈、不是在对你说话时，保持安静。用简短口语回答，问什么语言就用什么语言回答。"
)


def load_key() -> str:
    if key := os.environ.get("GEMINI_API_KEY"):
        return key
    for line in (Path.home() / ".config/givingear/env").read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到 GEMINI_API_KEY")


def synth(speaker: str, text: str, idx: int) -> np.ndarray:
    aiff = OUT / f"utt{idx}.aiff"
    wav = OUT / f"utt{idx}.wav"
    subprocess.run(["say", "-v", VOICES[speaker], "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(aiff), str(wav)], check=True)
    with wave.open(str(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def build_timeline():
    """返回整段 PCM 和每句话的 (说话人, 内容, 开始秒, 结束秒)。"""
    pieces, utts, t = [np.zeros(int(RATE * 1.0), np.int16)], [], 1.0
    for i, (spk, text, gap) in enumerate(SCRIPT):
        pcm = synth(spk, text, i)
        utts.append({"speaker": spk, "text": text, "start": t, "end": t + len(pcm) / RATE})
        t += len(pcm) / RATE + gap
        pieces += [pcm, np.zeros(int(RATE * gap), np.int16)]
    return np.concatenate(pieces), utts


async def run(mode: str, labels: bool, delay: float = LABEL_DELAY_S, run_id: str = "") -> None:
    OUT.mkdir(exist_ok=True)
    pcm, utts = build_timeline()
    client = genai.Client(api_key=load_key())
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    events, model_audio = [], []
    t0 = None

    def log(kind, **kw):
        events.append({"t": round(time.monotonic() - t0, 3), "kind": kind, **kw})

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        t0 = time.monotonic()
        log("connected")

        async def sender():
            chunk = RATE * CHUNK_MS // 1000
            pending = [u for u in utts] if labels else []
            for n in range(0, len(pcm), chunk):
                now = n / RATE
                while pending and now >= pending[0]["start"] + min(delay, pending[0]["end"] - pending[0]["start"]):
                    u = pending.pop(0)
                    label = f"[现在说话的是：{u['speaker']}]"
                    if mode == "realtime":
                        await session.send_realtime_input(text=label)
                    else:
                        await session.send_client_content(
                            turns=types.Content(role="user", parts=[types.Part(text=label)]), turn_complete=False
                        )
                    log("label_sent", speaker=u["speaker"], audio_t=round(now, 2))
                await session.send_realtime_input(
                    audio=types.Blob(data=pcm[n : n + chunk].tobytes(), mime_type=f"audio/pcm;rate={RATE}")
                )
                # 按实时速度发送
                target = t0 + (n + chunk) / RATE
                await asyncio.sleep(max(0, target - time.monotonic()))
            log("audio_done")
            await asyncio.sleep(3)

        async def receiver():
            in_model_turn = False
            # SDK 的 receive() 每收完一轮回复就结束，必须循环调用，否则消息堆积导致心跳超时断线
            while True:
                async for msg in session.receive():
                    sc = msg.server_content
                    if msg.data:
                        model_audio.append(msg.data)
                        if not in_model_turn:
                            in_model_turn = True
                            log("model_audio_start")
                    if msg.go_away:
                        log("go_away", time_left=str(msg.go_away.time_left))
                    if sc is None:
                        continue
                    if sc.input_transcription and sc.input_transcription.text:
                        log("in_text", text=sc.input_transcription.text)
                    if sc.output_transcription and sc.output_transcription.text:
                        log("out_text", text=sc.output_transcription.text)
                    if sc.interrupted:
                        log("interrupted")
                        in_model_turn = False
                    if sc.turn_complete:
                        log("turn_complete")
                        in_model_turn = False

        recv = asyncio.create_task(receiver())
        try:
            await sender()
        except Exception as exc:  # 出错时仍然输出已收到的事件，便于判断断在哪里
            log("error", error=repr(exc)[:200])
        finally:
            if recv.done() and recv.exception():
                log("receiver_error", error=repr(recv.exception())[:200])
            recv.cancel()

    tag = f"{mode}{'' if labels else '_nolabels'}_d{delay}{run_id}"
    (OUT / f"events_{tag}.json").write_text(json.dumps({"utts": utts, "events": events}, ensure_ascii=False, indent=1))
    with wave.open(str(OUT / f"model_{tag}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"".join(model_audio))
    summarize(utts, events)


def summarize(utts, events):
    print("== 时间线（音频时间轴，秒）==")
    for u in utts:
        print(f"  {u['start']:6.2f}-{u['end']:6.2f} [{u['speaker']}] {u['text']}")
    print("== 事件 ==")
    out_buf = []
    for e in events:
        if e["kind"] == "out_text":
            out_buf.append(e["text"])
            continue
        if out_buf:
            print(f"         AI: {''.join(out_buf)}")
            out_buf = []
        if e["kind"] == "in_text":
            continue
        extra = {k: v for k, v in e.items() if k not in ("t", "kind")}
        print(f"  {e['t']:6.2f} {e['kind']} {extra if extra else ''}")
    if out_buf:
        print(f"         AI: {''.join(out_buf)}")
    print("== 输入转写 ==")
    print("  " + "".join(e["text"] for e in events if e["kind"] == "in_text"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["realtime", "client"], default="realtime")
    p.add_argument("--no-labels", action="store_true")
    p.add_argument("--delay", type=float, default=LABEL_DELAY_S)
    p.add_argument("--run-id", default="")
    a = p.parse_args()
    asyncio.run(run(a.mode, not a.no_labels, a.delay, a.run_id))
