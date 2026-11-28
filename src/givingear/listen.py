"""只当耳朵：后台收音进程，以及控制它的客户端（givingear listen / transcript / who / name）。

后台进程录音、分辨说话人、在本地转写，不接任何 AI 模型；调用它的 AI 助手（Claude 等）充当大脑。
对外只在 127.0.0.1 的随机端口提供 HTTP 接口，每个请求都要带随机令牌；端口和令牌写在只有本人能读的状态文件里。
转写只在后台进程的内存里，停止后就没有了；不保存原始音频。
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import http.server
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .accounts import default_home
from .backend import NullBackend
from .events import EventBus
from .transcript import TranscriptLog

DEFAULT_MAX_MINUTES = 240  # 忘了停也不会一直录下去
START_TIMEOUT_S = 600  # 第一次启动要下载几百 MB 的模型


class ListenError(Exception):
    """给用户（和 AI 助手）看的错误。"""


def state_file() -> Path:
    return default_home() / "listen.json"


def log_file() -> Path:
    return default_home() / "listen.log"


# ---------- 后台进程 ----------


class ListenService:
    def __init__(self, session, source, close=lambda: None, max_minutes: float = DEFAULT_MAX_MINUTES, account: str | None = None):
        self.session, self.source, self._close = session, source, close
        self.bus: EventBus = session.bus
        self.log = TranscriptLog()
        self.max_minutes, self.account = max_minutes, account
        self.token = secrets.token_urlsafe(24)
        self.state = "listening"
        self.error = ""
        self.started = time.time()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self.httpd: http.server.ThreadingHTTPServer | None = None

    # 网页请求在线程里处理；涉及会话的操作交给事件循环做
    def call(self, fn):
        async def wrap():
            r = fn()
            return await r if asyncio.iscoroutine(r) else r

        return asyncio.run_coroutine_threadsafe(wrap(), self.loop).result(timeout=30)

    def status(self) -> dict:
        speakers = self.log.speakers()
        return {
            "state": self.state,
            "error": self.error,
            "account": self.account,
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "minutes": round((time.time() - self.started) / 60, 1),
            "max_minutes": self.max_minutes,
            "speakers": len(speakers),
            "utterances": len(self.log.lines),
            "pid": os.getpid(),
        }

    def name(self, body: dict) -> dict:
        async def do():
            result = self.session._call_tool("name_speaker", body)
            await self.session._send_roster_if_changed()
            return result

        return self.call(do)

    def register(self, body: dict) -> dict:
        return self.call(lambda: self.session.register_from_ui(str(body.get("speaker", "")), str(body.get("name", ""))))

    def request_stop(self) -> None:
        self.loop.call_soon_threadsafe(self._stop.set)

    async def run(self, port: int = 0, on_ready=None) -> None:
        self.loop = asyncio.get_running_loop()
        q = self.bus.subscribe()

        async def collect():
            while True:
                e = await q.get()
                self.log.handle(e)
                if e["type"] == "error":
                    self.error = e.get("message", "")

        collector = asyncio.create_task(collect())
        self.httpd = _make_server(self, port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        if on_ready:
            on_ready(self.httpd.server_address[1], self.token)

        listening = asyncio.create_task(self.session.run(self.source))
        stopper = asyncio.create_task(self._stop.wait())
        timeout = asyncio.create_task(asyncio.sleep(self.max_minutes * 60))
        try:
            done, _ = await asyncio.wait({listening, stopper, timeout}, return_when=asyncio.FIRST_COMPLETED)
            if listening in done:
                # 音频源结束（测试里的 WAV 放完了）或出错：继续提供查询，直到被要求停止
                self.state = "ended" if not listening.exception() else "error"
                if listening.exception():
                    self.error = self.error or str(listening.exception())
                await asyncio.wait({stopper, timeout}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (listening, stopper, timeout):
                t.cancel()
            await asyncio.gather(listening, return_exceptions=True)
            self._close()
            await asyncio.sleep(0.1)  # 让收集任务处理完最后几条事件
            collector.cancel()
            self.state = "stopped"
            self.httpd.shutdown()


def _make_server(service: ListenService, port: int) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 不把请求写进日志
            pass

        def _send(self, code: int, data) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got.encode(), f"Bearer {service.token}".encode())

        def _body(self) -> dict:
            n = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

        def do_GET(self):
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            url = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/status":
                return self._send(200, service.status())
            if url.path == "/transcript":
                since = float(qs["since"]) if qs.get("since") else None
                last = int(qs["last"]) if qs.get("last") else None
                rows = service.call(lambda: service.log.query(since, qs.get("speaker"), last))
                return self._send(200, {"lines": rows})
            if url.path == "/speakers":
                return self._send(200, {"speakers": service.call(service.log.speakers)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            path = urlparse(self.path).path
            if path == "/name":
                return self._send(200, service.name(self._body()))
            if path == "/register":
                return self._send(200, service.register(self._body()))
            if path == "/stop":
                self._send(200, {"ok": True})
                return service.request_stop()
            self._send(404, {"error": "not found"})

    return http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # 令牌只有自己能读
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def serve_main(argv: list[str] | None = None) -> None:
    """后台进程入口：python -m givingear.listen --serve ..."""
    p = argparse.ArgumentParser()
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES)
    p.add_argument("--account")
    p.add_argument("--wav", help="测试用：用 WAV 文件代替麦克风")
    args = p.parse_args(argv)
    path = state_file()

    async def main():
        from .session import Session

        vault = None
        if args.account:
            from .accounts import AccountStore

            password = sys.stdin.readline().rstrip("\n")  # 密码只从标准输入传进来，不出现在命令参数里
            vault = AccountStore().open(args.account, password)
        if args.wav:
            from .audio import WavFileSource

            source, close = WavFileSource(args.wav, realtime=True), lambda: None
        else:
            from .mac_audio import MacAudio

            mac = MacAudio(capture=True, echo_cancellation=False)  # 不播放声音，不需要回声消除
            source, close = mac, mac.close
        session = await asyncio.to_thread(Session, NullBackend(), EventBus(), vault=vault)
        service = ListenService(session, source, close, args.max_minutes, args.account)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, service.request_stop)

        def ready(port, token):
            _write_state(path, {"pid": os.getpid(), "port": port, "token": token, "started": time.time()})
            print(f"listening on 127.0.0.1:{port}", flush=True)

        try:
            await service.run(args.port, ready)
        finally:
            try:
                if json.loads(path.read_text()).get("pid") == os.getpid():
                    path.unlink()
            except (OSError, ValueError):
                pass

    asyncio.run(main())


# ---------- 客户端 ----------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_state() -> dict | None:
    path = state_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not _alive(int(data.get("pid", 0))):
        path.unlink(missing_ok=True)  # 进程已经不在了（比如电脑重启过），清掉过期的状态
        return None
    return data


def request(method: str, path: str, body: dict | None = None, state: dict | None = None, timeout: float = 30) -> dict:
    state = state or read_state()
    if not state:
        raise ListenError("现在没有在听。先运行：givingear listen start")
    req = urllib.request.Request(
        f"http://127.0.0.1:{state['port']}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {state['token']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError) as exc:
        raise ListenError(f"连不上后台收音进程：{exc}") from exc


def _log_tail(n: int = 15) -> str:
    try:
        return "\n".join(log_file().read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def start(account: str | None = None, max_minutes: float = DEFAULT_MAX_MINUTES, wav: str | None = None,
          password: str | None = None, timeout: float = START_TIMEOUT_S, on_wait=None) -> dict:
    """在后台开始收音；已经在听时直接返回当前状态。"""
    if (state := read_state()) is not None:
        return {"already_running": True, **request("GET", "/status", state=state)}
    if not wav and sys.platform == "darwin":
        from .mac_audio import ensure_mic_permission

        ensure_mic_permission()  # 第一次会弹出系统授权窗口，要在前台弹
    cmd = [sys.executable, "-m", "givingear.listen", "--serve", "--max-minutes", str(max_minutes)]
    if account:
        cmd += ["--account", account]
    if wav:
        cmd += ["--wav", str(wav)]
    log_file().parent.mkdir(parents=True, exist_ok=True)
    log = open(log_file(), "w")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True, text=True)
    proc.stdin.write((password or "") + "\n")
    proc.stdin.close()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise ListenError("后台收音进程启动失败：\n" + _log_tail())
        state = read_state()
        if state and state.get("pid") == proc.pid:
            return {"already_running": False, **request("GET", "/status", state=state)}
        if on_wait:
            on_wait()
        time.sleep(0.5)
    proc.terminate()
    raise ListenError("等待后台收音进程超时（第一次启动要下载模型，可能网络太慢）：\n" + _log_tail())


def stop(timeout: float = 15) -> dict:
    state = read_state()
    if not state:
        return {"ok": True, "was_running": False}
    status = request("GET", "/status", state=state)
    request("POST", "/stop", {}, state=state)
    deadline = time.time() + timeout
    while time.time() < deadline and _alive(state["pid"]):
        time.sleep(0.2)
    if _alive(state["pid"]):
        os.kill(state["pid"], signal.SIGTERM)
    return {"ok": True, "was_running": True, "minutes": status["minutes"], "utterances": status["utterances"]}


def status() -> dict:
    state = read_state()
    if not state:
        return {"state": "not_running"}
    return request("GET", "/status", state=state)


def transcript(since_minutes: float | None = None, speaker: str | None = None, last: int | None = None) -> list[dict]:
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in
                  (("since", since_minutes), ("speaker", speaker), ("last", last)) if v not in (None, ""))
    return request("GET", "/transcript" + (f"?{qs}" if qs else ""))["lines"]


def speakers() -> list[dict]:
    return request("GET", "/speakers")["speakers"]


def name(speaker: str, new_name: str, aliases: list[str] | None = None) -> dict:
    return request("POST", "/name", {"speaker": speaker, "name": new_name, "aliases": aliases or []})


def register(speaker: str, new_name: str = "") -> dict:
    return request("POST", "/register", {"speaker": speaker, "name": new_name})


if __name__ == "__main__":
    serve_main()
