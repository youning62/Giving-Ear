"""命令行外壳：启动核心，订阅事件流，在终端里滚动显示带说话人标签的对话。"""

from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import sys
import threading
import webbrowser
from pathlib import Path

from .audio import WavFileSource
from .backend import FakeBackend
from .config import ConfigError, gemini_api_key
from .events import EventBus
from .server import DEFAULT_PORT, start_server
from .session import Session


class Renderer:
    """把事件变成终端里的文字。转写是一小段一小段到达的，同一个人的话接在同一行。"""

    def __init__(self, verbose: bool = False, out=None):
        self.verbose = verbose
        self.out = out or sys.stdout
        self._line_owner: str | None = None  # 当前这一行是谁的话

    def _write(self, s: str) -> None:
        self.out.write(s)
        self.out.flush()

    def _line(self, s: str) -> None:
        self._end_line()
        self._write(s + "\n")

    def _end_line(self) -> None:
        if self._line_owner is not None:
            self._write("\n")
            self._line_owner = None

    def _append(self, owner: str, text: str) -> None:
        if owner != self._line_owner:
            self._end_line()
            self._write(f"{owner}: ")
            self._line_owner = owner
        self._write(text)

    def handle(self, e: dict) -> None:
        t = f"{e['audio_t']:7.1f}s" if "audio_t" in e else " " * 8
        match e["type"]:
            case "status":
                text = {
                    "listening": "● 正在听……（按 Ctrl+C 退出）",
                    "stopped": "■ 已停止",
                    "session_ending": "！与 AI 的会话即将达到时长上限",
                }.get(e["state"], e["state"])
                self._line(text)
            case "profile_renamed":
                self._line(f"         ✎ {e['old_name']} → {e['name']}")
            case "name_hint":
                self._line(f"         ？“{e['heard']}”可能是指 {e['name']}")
            case "profile_created":
                self._line(f"{t}  + 新的声音：{e['name']}")
            case "speaker_guess" if self.verbose:
                score = "新的声音" if e["new"] else f"相似度 {e['score']:.2f}"
                self._line(f"{t}  [{e['name']}] 开口（{score}）")
            case "speech_end" if self.verbose:
                self._line(f"{t}  [{e['name']}] 说完（{e['audio_t'] - e['start']:.1f} 秒）")
            case "transcript":
                self._append(f"[{e['name']}]", e["text"])
                self._end_line()
            case "model_transcript" if self.verbose:
                self._line(f"         （模型转写）{e['text']}")
            case "ai_text":
                self._append("AI", e["text"])
            case "ai_interrupted":
                self._append("AI", "（被打断）")
                self._end_line()
            case "ai_turn_end":
                if self._line_owner == "AI":
                    self._end_line()
            case "error":
                self._line(f"！{e['message']}")


WEB_DIR = Path(__file__).parent / "web"
WEB_PORT = 8766


def _serve_web(port: int) -> http.server.ThreadingHTTPServer:
    """在本机提供可视化网页（只监听 127.0.0.1），页面自己去连 WebSocket 事件流。"""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # 不往终端里刷请求日志
            pass

    handler = functools.partial(Handler, directory=str(WEB_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


async def _render(q: asyncio.Queue, renderer: Renderer) -> None:
    while True:
        e = await q.get()
        renderer.handle(e)
        if e["type"] == "closed":
            return


async def _run(args: argparse.Namespace) -> None:
    mac = None
    if args.fake:
        backend = FakeBackend()
    else:
        from .gemini import GeminiBackend

        backend = GeminiBackend(gemini_api_key())

    if args.wav:
        source = WavFileSource(args.wav, realtime=args.realtime or not args.fake)  # 接真模型时必须按真实速度
        if not args.fake:
            from .mac_audio import MacAudio

            mac = MacAudio(capture=False)
    else:
        from .mac_audio import MacAudio, ensure_mic_permission

        ensure_mic_permission()
        mac = MacAudio(capture=True, echo_cancellation=args.aec == "on")
        source = mac

    bus = EventBus()
    server = await start_server(bus, port=args.port) if args.port else None
    web = None
    if args.web:
        if not args.port:
            sys.exit("！--web 需要事件流端口，不能同时用 --port 0")
        web = _serve_web(WEB_PORT)
        url = f"http://127.0.0.1:{WEB_PORT}/?ws=ws://127.0.0.1:{args.port}"
        print(f"网页：{url}")
        webbrowser.open(url)
        for _ in range(300):  # 等网页连上事件流再开始，免得 WAV 几秒就放完、页面什么都没看到
            if server.connections:
                break
            await asyncio.sleep(0.1)
    renderer = Renderer(verbose=args.verbose or args.fake)
    printer = asyncio.create_task(_render(bus.subscribe(), renderer))  # 先订阅再启动核心，不漏掉第一条事件
    session = Session(backend, bus, sink=mac)
    try:
        await session.run(source)
    finally:
        bus.close()
        await printer
        if mac:
            mac.close()
        if server:
            server.close()
            await server.wait_closed()
        if web:
            print("会话已结束，网页仍可查看，按 Ctrl+C 退出")
            await asyncio.Event().wait()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="givingear", description="给 AI 一只耳朵：分辨是谁在讲话")
    sub = parser.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="开始听（默认）")
    run.add_argument("--wav", help="用 WAV 文件代替麦克风")
    run.add_argument("--realtime", action="store_true", help="按真实速度播放 WAV（接真模型时总是按真实速度）")
    run.add_argument("--fake", action="store_true", help="不连接 AI，用假后端（测试用）")
    run.add_argument("--aec", choices=["on", "off"], default="on", help="系统回声消除；戴耳机时可以关闭")
    run.add_argument("--verbose", "-v", action="store_true", help="显示每次说话人判断的细节")
    run.add_argument("--web", action="store_true", help="打开本地可视化网页")
    run.add_argument("--port", type=int, default=DEFAULT_PORT, help="本地事件流端口，0 表示不开启")
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in sub.choices:  # 不写子命令时默认是 run
        argv = ["run", *argv]
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args))
    except ConfigError as exc:
        sys.exit(f"！{exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
