"""01 验证：Gemini Live 单个音频会话的时长上限。持续发送静音，记录 go_away 通知和断线时间。"""
import asyncio, sys, time
from google import genai
from google.genai import types
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from label_timing import load_key, MODEL, RATE

MAX_S = 40 * 60

async def main():
    client = genai.Client(api_key=load_key())
    config = types.LiveConnectConfig(response_modalities=["AUDIO"])
    t0 = time.monotonic()
    el = lambda: f"{time.monotonic() - t0:7.1f}s"
    silence = bytes(RATE * 2 // 10)  # 100ms 静音
    try:
        async with client.aio.live.connect(model=MODEL, config=config) as s:
            print(el(), "connected", flush=True)
            async def recv():
                while True:
                    async for m in s.receive():
                        if m.go_away:
                            print(el(), "go_away time_left=", m.go_away.time_left, flush=True)
            r = asyncio.create_task(recv())
            last = 0
            while time.monotonic() - t0 < MAX_S:
                await s.send_realtime_input(audio=types.Blob(data=silence, mime_type=f"audio/pcm;rate={RATE}"))
                await asyncio.sleep(0.1)
                if time.monotonic() - t0 - last > 60:
                    last = time.monotonic() - t0; print(el(), "alive", flush=True)
                if r.done():
                    print(el(), "receiver ended:", repr(r.exception())[:300], flush=True); break
    except Exception as e:
        print(el(), "closed:", repr(e)[:300], flush=True)
    print(el(), "end", flush=True)

asyncio.run(main())
