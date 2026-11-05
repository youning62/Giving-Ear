"""本地网页服务：提供页面和账号接口。只监听 127.0.0.1。

安全约定：
- 登录令牌随机生成，只放在内存里，服务一重启就失效；密码不落盘、不写日志。
- 每个请求都校验 Host（防止别的网站通过 DNS 重绑定访问本地服务）和 Origin（防止别的网站发请求）。
- 写操作必须是 JSON（跨站请求做不到不触发预检的 JSON 请求）。
- 同一个账号连续输错密码会被暂时锁定。
"""

from __future__ import annotations

import functools
import http.server
import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import models
from .accounts import AccountError, AccountStore, NoSuchAccount, Vault, WrongPassword

WEB_DIR = Path(__file__).parent / "web"
MAX_FAILURES, LOCK_SECONDS = 5, 300
MAX_BODY = 64 * 1024


class WebApp:
    def __init__(self, store: AccountStore):
        self.store = store
        self.tokens: dict[str, Vault] = {}
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def locked(self, username: str) -> bool:
        now = time.time()
        recent = [t for t in self._failures.get(username, []) if now - t < LOCK_SECONDS]
        self._failures[username] = recent
        return len(recent) >= MAX_FAILURES

    def login(self, username: str, password: str) -> str:
        with self._lock:
            name = username.strip().casefold()
            if self.locked(name):
                raise AccountError("输错太多次，请 5 分钟后再试")
            try:
                vault = self.store.open(username, password)
            except (WrongPassword, NoSuchAccount) as exc:
                self._failures.setdefault(name, []).append(time.time())
                raise AccountError("用户名或密码不对") from exc  # 不透露是哪个错了
            token = secrets.token_urlsafe(32)
            self.tokens[token] = vault
            return token


def make_handler(app: WebApp, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # 不往终端里刷请求日志（也避免日志里出现任何敏感内容）
            pass

        # ---- 工具 ----
        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _safe(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._json(403, {"error": "不允许的来源"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                self._json(403, {"error": "不允许的来源"})
                return False
            return True

        def _body(self) -> dict | None:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json(415, {"error": "请求必须是 JSON"})
                return None
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._json(413, {"error": "请求太大"})
                return None
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                self._json(400, {"error": "请求格式不对"})
                return None
            return body if isinstance(body, dict) else None

        def _vault(self) -> Vault | None:
            auth = self.headers.get("Authorization", "")
            vault = app.tokens.get(auth.removeprefix("Bearer ")) if auth.startswith("Bearer ") else None
            if vault is None:
                self._json(401, {"error": "请先登录"})
                return None
            vault.reload()
            return vault

        def _handle(self, fn) -> None:
            try:
                fn()
            except AccountError as exc:
                self._json(400, {"error": str(exc)})

        # ---- 路由 ----
        def do_GET(self):
            if not self._safe():
                return
            path = urlparse(self.path).path
            if path == "/api/me":
                return self._me()
            if path == "/api/speakers":
                return self._list()
            if path.startswith("/api/"):
                return self._json(404, {"error": "没有这个接口"})
            super().do_GET()

        def do_POST(self):
            if not self._safe():
                return
            routes = {"/api/register": self._register, "/api/login": self._login, "/api/logout": self._logout,
                      "/api/speakers/rename": self._rename}
            fn = routes.get(urlparse(self.path).path)
            if fn is None:
                return self._json(404, {"error": "没有这个接口"})
            self._handle(fn)

        def do_DELETE(self):
            if not self._safe():
                return
            path = urlparse(self.path).path
            if path.startswith("/api/speakers/"):
                return self._handle(lambda: self._delete(unquote(path.removeprefix("/api/speakers/"))))
            self._json(404, {"error": "没有这个接口"})

        def _register(self):
            if (b := self._body()) is None:
                return
            app.store.create(str(b.get("username", "")), str(b.get("password", "")), model=models.SPEAKER_MODEL[0])
            self._json(200, {"token": app.login(str(b["username"]), str(b["password"]))})

        def _login(self):
            if (b := self._body()) is None:
                return
            try:
                token = app.login(str(b.get("username", "")), str(b.get("password", "")))
            except AccountError as exc:
                return self._json(429 if "太多次" in str(exc) else 401, {"error": str(exc)})
            self._json(200, {"token": token})

        def _logout(self):
            auth = self.headers.get("Authorization", "").removeprefix("Bearer ")
            app.tokens.pop(auth, None)
            self._json(200, {"ok": True})

        def _me(self):
            if (v := self._vault()) is not None:
                self._json(200, {"username": v.username, "count": len(v.voiceprints)})

        def _list(self):
            if (v := self._vault()) is not None:
                self._json(200, {"username": v.username, "speakers": [
                    {"name": s.name, "aliases": s.aliases, "registered_at": s.registered_at} for s in v.voiceprints]})

        def _rename(self):
            if (v := self._vault()) is None or (b := self._body()) is None:
                return
            v.rename(str(b.get("old", "")), str(b.get("new", "")))
            v.save()
            self._json(200, {"ok": True})

        def _delete(self, name: str):
            if (v := self._vault()) is None:
                return
            if not v.remove(name):
                return self._json(404, {"error": f"没有叫“{name}”的档案"})
            v.save()
            self._json(200, {"ok": True})

    return Handler


def serve(port: int, store: AccountStore | None = None) -> http.server.ThreadingHTTPServer:
    app = WebApp(store or AccountStore())
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), lambda *a, **k: None)
    httpd.app = app
    real_port = httpd.server_address[1]
    httpd.RequestHandlerClass = functools.partial(make_handler(app, real_port), directory=str(WEB_DIR))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
