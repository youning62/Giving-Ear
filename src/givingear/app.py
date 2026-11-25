"""网页应用：一个一直运行的服务，会话的开始和停止都由网页控制。

`Controller` 管理"当前会话"：同一时间最多一个；开始时接上麦克风和语音模型，停止或结束后回到空闲。
音频源和后端通过工厂函数创建，测试里换成 WAV 文件和假后端。
"""

from __future__ import annotations

import asyncio
import errno
import json
import urllib.request
import webbrowser
from typing import Awaitable, Callable

from .accounts import AccountStore, Vault
from .audio import AudioSource
from .backend import VoiceBackend
from .config import ConfigError
from .events import EventBus, event
from .server import DEFAULT_PORT, start_server
from .session import AudioSink, Session
from . import webapi

WEB_PORT = 8766


class AppError(Exception):
    """给用户看的错误，消息直接显示在网页上。"""


class Controller:
    def __init__(
        self,
        bus: EventBus,
        backend_factory: Callable[[Vault | None], VoiceBackend],
        source_factory: Callable[[bool], Awaitable[tuple[AudioSource, AudioSink | None, Callable[[], None]]]],
        session_options: dict | None = None,
    ):
        self._session_options = session_options or {}
        self.bus = bus
        self._backend_factory = backend_factory
        self._source_factory = source_factory
        self._session: Session | None = None
        self._task: asyncio.Task | None = None
        self._starting = False
        self.account: str | None = None

    @property
    def state(self) -> str:
        if self._starting:
            return "starting"
        return "listening" if self._task and not self._task.done() else "idle"

    async def start(self, vault: Vault | None = None, aec: bool = True) -> None:
        if self.state != "idle":
            raise AppError("已经在监听了")
        self._starting = True
        close = lambda: None
        try:
            backend = self._backend_factory(vault)
            source, sink, close = await self._source_factory(aec)
            # 第一次要下载并加载本地模型（几百 MB），放到线程里做，不卡住网页服务
            session = await asyncio.to_thread(Session, backend, self.bus, sink=sink, vault=vault, **self._session_options)
        except (ConfigError, AppError) as exc:
            close()  # 已经打开的麦克风要释放
            raise AppError(str(exc)) from exc
        finally:
            self._starting = False
        self.bus.reset()
        self.account = vault.username if vault else None
        self.bus.publish(event("session_started", account=self.account))
        self._session = session
        self._task = asyncio.create_task(self._run(self._session, source, close))

    async def _run(self, session: Session, source: AudioSource, close: Callable[[], None]) -> None:
        try:
            await session.run(source)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Session.run 已经把错误发到事件流里了
        finally:
            close()

    async def stop(self) -> None:
        task = self._task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._session = None

    async def register(self, speaker_id: str, name: str = "") -> dict:
        if self.state != "listening" or self._session is None:
            raise AppError("还没有开始监听")
        return await self._session.register_from_ui(speaker_id, name)


# ---- 真实的音频和后端 ----


def real_backend(vault: Vault | None) -> VoiceBackend:
    """按设置里选的提供商创建后端：Gemini 是原生语音；其他是"文字模型 + 本机语音合成"。"""
    from .config import read_key
    from .prompts import system_prompt
    from .providers import PROVIDERS, load_settings
    from .tools import ACCOUNT_TOOLS, ALL_TOOLS

    settings = load_settings()
    provider = PROVIDERS[settings.provider]
    key = read_key(provider.key_env)
    if not key:
        raise AppError(f"还没有设置「{provider.label}」的密钥。请先点右上角的“设置”填写")
    tools = ACCOUNT_TOOLS if vault else ALL_TOOLS
    if provider.kind == "live":
        from .gemini import GeminiBackend

        return GeminiBackend(key, system_prompt=system_prompt(vault is not None), tools=tools)
    from .chat_backend import TextChatBackend
    from .openai_client import OpenAIChatClient
    from .voices import make_synthesizer

    try:
        base_url, model = settings.endpoint()
        synth, emotional = make_synthesizer(settings)
    except ConfigError as exc:
        raise AppError(str(exc)) from exc
    return TextChatBackend(
        OpenAIChatClient(base_url, key, model), synth, system_prompt(vault is not None, text_mode=True, emotional=emotional), tools
    )


async def real_source(aec: bool):
    from .mac_audio import MacAudio, ensure_mic_permission

    await asyncio.to_thread(ensure_mic_permission)  # 第一次会弹出系统授权窗口，不能卡住事件循环
    mac = MacAudio(capture=True, echo_cancellation=aec)
    return mac, mac, mac.close


def already_running(web_port: int) -> bool:
    """这个端口上跑的是不是另一个 GivingEar（而不是别的程序）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{web_port}/api/state", timeout=2) as r:
            return "state" in json.loads(r.read())
    except (OSError, ValueError):
        return False


async def _port_busy(web_port: int, port: int, open_browser: bool) -> None:
    if await asyncio.to_thread(already_running, web_port):
        url = f"http://127.0.0.1:{web_port}/"
        print(f"GivingEar 已经在运行：{url}")
        if open_browser:
            webbrowser.open(url)
        return
    raise ConfigError(
        f"端口 {port} 已经被别的程序占用了。可以关掉占用它的程序（可能是上次没关掉的 GivingEar 窗口），"
        "或者换个端口启动：givingear web --web-port 9000 --port 9001"
    )


async def run_app(web_port: int = WEB_PORT, ws_port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    bus = EventBus()
    controller = Controller(bus, real_backend, real_source)
    try:
        ws = await start_server(bus, port=ws_port)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        return await _port_busy(web_port, ws_port, open_browser)
    try:
        httpd = webapi.serve(web_port, AccountStore(), controller=controller, loop=asyncio.get_running_loop(), ws_port=ws_port)
    except OSError as exc:
        ws.close()
        if exc.errno != errno.EADDRINUSE:
            raise
        return await _port_busy(web_port, web_port, open_browser)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"GivingEar 已启动：{url}\n所有操作都在网页里完成。关闭这个窗口或按 Ctrl+C 退出。")
    if open_browser:
        webbrowser.open(url)
    try:
        await asyncio.Event().wait()
    finally:
        await controller.stop()
        httpd.shutdown()
        ws.close()
