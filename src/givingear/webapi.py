"""本地网页服务：提供页面和账号接口。只监听 127.0.0.1。

安全约定：
- 登录令牌随机生成，只放在内存里，服务一重启就失效；密码不落盘、不写日志。
- 每个请求都校验 Host（防止别的网站通过 DNS 重绑定访问本地服务）和 Origin（防止别的网站发请求）。
- 写操作必须是 JSON（跨站请求做不到不触发预检的 JSON 请求）。
- 同一个账号连续输错密码会被暂时锁定。
"""

from __future__ import annotations

import asyncio
import base64
import functools
import http.server
import io
import json
import secrets
import threading
import time
import wave
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config, models, providers
from .emotions import EMOTIONS
from .accounts import AccountError, AccountStore, NoSuchAccount, Vault, WrongPassword

WEB_DIR = Path(__file__).parent / "web"
API_VERSION = 4  # 接口有不兼容的变化时加 1；网页里的 EXPECTED_API 必须同步（有测试检查）
MAX_FAILURES, LOCK_SECONDS = 5, 300
MAX_BODY = 64 * 1024


def _real_chat_client(settings: providers.Settings):
    from .openai_client import OpenAIChatClient

    provider = providers.PROVIDERS[settings.provider]
    base_url, model = settings.endpoint()
    return OpenAIChatClient(base_url, config.read_key(provider.key_env), model, timeout=20)


def _make_synth(settings: providers.Settings):
    from .voices import make_synthesizer

    return make_synthesizer(settings)[0]


class WebApp:
    def __init__(self, store: AccountStore, controller=None, loop=None, ws_port: int | None = None):
        self.store = store
        self.controller, self.loop, self.ws_port = controller, loop, ws_port
        self.chat_client_factory = _real_chat_client  # 测试里换成假的
        self.synth_factory = lambda s: _make_synth(s)
        self.tokens: dict[str, Vault] = {}
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def run(self, coro):
        """网页请求在线程里处理，会话在事件循环里跑：把协程交给事件循环执行并等结果。"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=60)

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

        def _optional_vault(self) -> Vault | None:
            auth = self.headers.get("Authorization", "")
            vault = app.tokens.get(auth.removeprefix("Bearer ")) if auth.startswith("Bearer ") else None
            if vault is not None:
                vault.reload()
            return vault

        def _handle(self, fn) -> None:
            from .app import AppError
            from .config import ConfigError

            try:
                fn()
            except AccountError as exc:
                self._json(400, {"error": str(exc)})
            except ConfigError as exc:
                self._json(400, {"error": str(exc)})
            except AppError as exc:
                self._json(409, {"error": str(exc)})

        # ---- 路由 ----
        def do_GET(self):
            if not self._safe():
                return
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._state()
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
                      "/api/speakers/rename": self._rename, "/api/settings/key": self._set_key,
                      "/api/settings/provider": self._set_provider, "/api/settings/test": self._test,
                      "/api/settings/test-voice": self._test_voice,
                      "/api/session/start": self._start, "/api/session/stop": self._stop,
                      "/api/session/register": self._register_now}
            fn = routes.get(urlparse(self.path).path)
            if fn is None:
                return self._json(404, {"error": "没有这个接口"})
            self._handle(fn)

        def do_DELETE(self):
            if not self._safe():
                return
            path = urlparse(self.path).path
            if path == "/api/settings/key":
                return self._handle(self._delete_key)
            if path.startswith("/api/speakers/"):
                return self._handle(lambda: self._delete(unquote(path.removeprefix("/api/speakers/"))))
            self._json(404, {"error": "没有这个接口"})

        def _provider_info(self) -> dict:
            s = providers.load_settings()
            return {
                "provider": s.provider,
                "providers": [
                    {"id": p.id, "label": p.label, "kind": p.kind, "note": p.note, **config.key_status(p.key_env)}
                    for p in providers.PROVIDERS.values()
                ],
                "settings": {k: v for k, v in vars(s).items() if k != "provider"},
                "tts_engines": [{"id": k, "label": v} for k, v in providers.TTS_ENGINES.items()],
                "tts_key": config.key_status(providers.TTS_KEY_ENV),
                "emotions": list(EMOTIONS),
            }

        def _state(self):
            c = app.controller
            info = self._provider_info()
            current = next(p for p in info["providers"] if p["id"] == info["provider"])
            self._json(200, {"api_version": API_VERSION, "state": c.state if c else "unavailable", "account": c.account if c else None,
                             "ws_port": app.ws_port, "has_key": current["has_key"], "hint": current["hint"], **info})

        def _provider_for(self, name) -> providers.Provider:
            if name == "dashscope":  # 通义千问语音合成的密钥，不是文字模型
                return providers.Provider("dashscope", "通义千问语音合成", "tts", providers.TTS_KEY_ENV)
            p = providers.PROVIDERS.get(str(name or "gemini"))
            if p is None:
                raise config.ConfigError("不支持这个模型提供商")
            return p

        def _set_key(self):
            if (b := self._body()) is None:
                return
            p = self._provider_for(b.get("provider"))
            config.save_api_key(p.key_env, str(b.get("key", "")))
            self._json(200, config.key_status(p.key_env))

        def _delete_key(self):
            p = self._provider_for(parse_qs(urlparse(self.path).query).get("provider", ["gemini"])[0])
            config.delete_api_key(p.key_env)
            self._json(200, config.key_status(p.key_env))

        def _set_provider(self):
            if (b := self._body()) is None:
                return
            s = providers.load_settings()
            for field in ("provider", "deepseek_model", "custom_base_url", "custom_model", "zh_voice", "en_voice", "tts_engine",
                          "qwen_voice", "qwen_model", "qwen_base_url", "cosyvoice_url", "cosyvoice_mode", "cosyvoice_speaker", "cosyvoice_rate"):
                if field in b:
                    if not isinstance(b[field], str):
                        raise config.ConfigError("设置的格式不对")
                    setattr(s, field, b[field].strip())
            if s.provider == "custom" and (not s.custom_base_url or not s.custom_model):
                raise config.ConfigError("自定义模型需要同时填写接口地址和模型名")
            providers.save_settings(s)
            self._json(200, self._provider_info())

        def _test_voice(self):
            if (b := self._body()) is None:
                return
            emotion = b.get("emotion") or None
            if emotion is not None and emotion not in EMOTIONS:
                raise config.ConfigError("不认识这个情绪")
            text = str(b.get("text") or "你好呀，今天过得怎么样？")[:200]
            s = providers.load_settings()
            from .tts import SynthError

            async def go():
                synth = app.synth_factory(s)
                try:
                    return await synth.synth(text, emotion)
                finally:
                    if hasattr(synth, "aclose"):
                        await synth.aclose()

            try:
                pcm, rate = asyncio.run(go())
            except (SynthError, config.ConfigError) as exc:
                return self._json(200, {"ok": False, "message": str(exc)})
            if not pcm:
                return self._json(200, {"ok": False, "message": "没有合成出声音"})
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm)
            self._json(200, {"ok": True, "wav": base64.b64encode(buf.getvalue()).decode(), "emotion": emotion})

        def _test(self):
            s = providers.load_settings()
            p = providers.PROVIDERS[s.provider]
            if p.kind == "live":
                return self._json(200, {"ok": True, "message": "Gemini 是原生语音模型，没有单独的测试；开始监听时会直接连接"} if config.read_key(p.key_env)
                                  else {"ok": False, "message": "还没有设置 Gemini 的密钥"})
            if not config.read_key(p.key_env):
                return self._json(200, {"ok": False, "message": f"还没有设置「{p.label}」的密钥"})
            from .openai_client import ChatError

            async def go():
                client = app.chat_client_factory(s)
                try:
                    return await client.ping()
                finally:
                    await client.aclose()

            try:
                reply = asyncio.run(go())
            except (ChatError, config.ConfigError) as exc:
                return self._json(200, {"ok": False, "message": str(exc)})
            self._json(200, {"ok": True, "message": f"连接成功，模型回复：{reply}"})

        def _need_controller(self):
            if app.controller is None:
                self._json(404, {"error": "这个服务没有开启会话控制"})
            return app.controller

        def _start(self):
            if (c := self._need_controller()) is None or (b := self._body()) is None:
                return
            app.run(c.start(self._optional_vault(), aec=bool(b.get("aec", True))))
            self._json(200, {"state": c.state, "account": c.account})

        def _stop(self):
            if (c := self._need_controller()) is None:
                return
            app.run(c.stop())
            self._json(200, {"state": c.state})

        def _register_now(self):
            if (c := self._need_controller()) is None or (v := self._vault()) is None or (b := self._body()) is None:
                return
            if c.account != v.username:
                return self._json(409, {"error": "当前监听用的不是这个账号。请停止后，登录这个账号再开始监听"})
            self._json(200, app.run(c.register(str(b.get("speaker_id", "")), str(b.get("name", "")))))

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


def serve(port: int, store: AccountStore | None = None, controller=None, loop=None, ws_port: int | None = None) -> http.server.ThreadingHTTPServer:
    app = WebApp(store or AccountStore(), controller, loop, ws_port)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), lambda *a, **k: None)
    httpd.app = app
    real_port = httpd.server_address[1]
    httpd.RequestHandlerClass = functools.partial(make_handler(app, real_port), directory=str(WEB_DIR))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
