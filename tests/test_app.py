"""网页应用：会话由 Controller 管理（开始、停止、再开始），网页接口只是它的外壳。"""

import asyncio
import json
import os
import stat
import threading
import time

import pytest
from websockets.asyncio.client import connect

from givingear import webapi
from givingear.accounts import AccountStore
from givingear.app import AppError, Controller, real_backend
from givingear.audio import WavFileSource
from givingear.backend import FakeBackend
from givingear.events import EventBus
from givingear.server import start_server
from test_webapi import FAST, PW, call, signed_up


class HoldOpen:
    """放完音频后不结束，像真的麦克风一样一直开着，直到被停止。"""

    def __init__(self, source):
        self.source = source

    async def frames(self):
        async for f in self.source.frames():
            yield f
        await asyncio.Event().wait()


def make_controller(path, realtime=False, hold=False, **options):
    closed = []

    async def source_factory(aec):
        src = WavFileSource(path, realtime=realtime)
        return (HoldOpen(src) if hold else src), None, lambda: closed.append(True)

    c = Controller(EventBus(), lambda vault: FakeBackend(), source_factory, session_options=options)
    c.closed = closed
    return c


async def until(cond, timeout=60):
    end = time.time() + timeout
    while not cond():
        assert time.time() < end, "等太久了"
        await asyncio.sleep(0.05)


def types(c):
    return [e["type"] for e in c.bus.history]


async def test_a_session_that_runs_out_of_audio_returns_to_idle(conversation):
    c = make_controller(conversation.path)
    assert c.state == "idle"
    await c.start()
    await until(lambda: c.state == "idle")
    assert types(c)[0] == "session_started" and "transcript" in types(c) and types(c)[-1] == "status"
    assert c.closed == [True]  # 麦克风/扬声器被释放了


async def test_stopping_in_the_middle_and_starting_again(conversation):
    c = make_controller(conversation.path, realtime=True)
    await c.start()
    assert c.state == "listening"
    await until(lambda: "speech_start" in types(c))
    await c.stop()
    assert c.state == "idle" and c.closed == [True]
    assert [e for e in c.bus.history if e["type"] == "status"][-1]["state"] == "stopped"
    await c.start()  # 可以再开始，而且是一个新的会话（历史已清空）
    assert c.state == "listening" and types(c)[0] == "session_started" and types(c).count("session_started") == 1
    await c.stop()


async def test_starting_twice_is_refused(conversation):
    c = make_controller(conversation.path, hold=True)
    await c.start()
    with pytest.raises(AppError, match="已经在监听"):
        await c.start()
    await c.stop()


async def test_stopping_when_idle_is_harmless(conversation):
    c = make_controller(conversation.path)
    await c.stop()
    assert c.state == "idle"


async def test_a_failure_to_open_the_microphone_is_reported_and_leaves_it_idle(conversation):
    from givingear.config import ConfigError

    async def broken(aec):
        raise ConfigError("没有麦克风权限")

    c = Controller(EventBus(), lambda v: FakeBackend(), broken)
    with pytest.raises(AppError, match="没有麦克风权限"):
        await c.start()
    assert c.state == "idle"


async def test_a_logged_in_session_knows_registered_people(conversation, tmp_path):
    from test_session import harvest_voiceprints, make_vault

    found = await harvest_voiceprints(conversation)
    _, vault = make_vault(tmp_path, {"小王": found["s1"]})
    c = make_controller(conversation.path)
    await c.start(vault)
    await until(lambda: c.state == "idle")
    assert c.account == "wang" and any(e["type"] == "account_loaded" for e in c.bus.history)
    assert [e["name"] for e in c.bus.history if e["type"] == "speech_end"][0] == "小王"


async def test_registering_from_the_page_saves_to_the_account(conversation, tmp_path):
    from test_session import make_vault

    store, vault = make_vault(tmp_path, {})
    c = make_controller(conversation.path, hold=True, min_register_s=0)
    await c.start(vault)
    await until(lambda: sum(t == "speech_end" for t in types(c)) >= 6)
    result = await c.register("s1", "小王")
    assert result["ok"] is True, result
    assert [v.name for v in store.open("wang", PW).voiceprints] == ["小王"]
    assert "registered" in types(c)
    await c.stop()


async def test_registering_as_a_guest_is_refused(conversation):
    c = make_controller(conversation.path, hold=True, min_register_s=0)
    await c.start()
    await until(lambda: "speech_end" in types(c))
    result = await c.register("s1", "小王")
    assert result["ok"] is False and "登录" in result["error"]
    await c.stop()


async def test_registering_before_listening_is_refused(conversation):
    with pytest.raises(AppError, match="还没有开始监听"):
        await make_controller(conversation.path).register("s1", "小王")


async def test_a_late_browser_gets_what_already_happened(conversation):
    c = make_controller(conversation.path, hold=True)
    server = await start_server(c.bus, port=0)
    port = next(iter(server.sockets)).getsockname()[1]
    await c.start()
    await until(lambda: "transcript" in types(c))
    async with connect(f"ws://127.0.0.1:{port}") as ws:  # 会话进行到一半才打开网页
        first = [json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] for _ in range(3)]
    assert first[0] == "session_started"
    await c.stop()
    server.close()


def test_real_backend_without_a_key_says_what_to_do(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GIVINGEAR_CONFIG", str(tmp_path / "nothing"))
    with pytest.raises(AppError, match="设置"):
        real_backend(None)


# ---- 网页接口 ----


@pytest.fixture
def web(tmp_path, conversation, monkeypatch):
    monkeypatch.setenv("GIVINGEAR_CONFIG", str(tmp_path / "config" / "env"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    box = {}

    async def setup():
        box["c"] = make_controller(conversation.path, hold=True, min_register_s=0)

    asyncio.run_coroutine_threadsafe(setup(), loop).result()
    store = AccountStore(tmp_path, kdf_cost=FAST)
    httpd = webapi.serve(0, store, controller=box["c"], loop=loop, ws_port=8765)
    yield httpd, box["c"], tmp_path
    asyncio.run_coroutine_threadsafe(box["c"].stop(), loop).result()
    httpd.shutdown()
    httpd.server_close()
    loop.call_soon_threadsafe(loop.stop)


def wait(cond, timeout=60):
    end = time.time() + timeout
    while not cond():
        assert time.time() < end, "等太久了"
        time.sleep(0.05)


def test_state_start_stop_through_the_page(web):
    httpd, c, _ = web
    state = call(httpd, "GET", "/api/state")[1]
    assert {k: state[k] for k in ("state", "account", "ws_port", "has_key", "hint", "provider")} == {
        "state": "idle", "account": None, "ws_port": 8765, "has_key": False, "hint": "", "provider": "gemini"}
    assert call(httpd, "POST", "/api/session/start", {})[0] == 200
    assert call(httpd, "GET", "/api/state")[1]["state"] == "listening"
    status, body = call(httpd, "POST", "/api/session/start", {})
    assert status == 409 and "已经在监听" in body["error"]
    assert call(httpd, "POST", "/api/session/stop", {})[1] == {"state": "idle"}
    assert call(httpd, "POST", "/api/session/start", {})[0] == 200  # 停止后可以再开始


def test_starting_while_logged_in_uses_that_account(web):
    httpd, c, _ = web
    token = signed_up(httpd)
    call(httpd, "POST", "/api/session/start", {}, token)
    assert call(httpd, "GET", "/api/state")[1]["account"] == "wang"


def test_registering_from_the_page_needs_login_and_the_same_account(web):
    httpd, c, _ = web
    token = signed_up(httpd)
    call(httpd, "POST", "/api/session/start", {})  # 以访客身份开始
    wait(lambda: any(e["type"] == "speech_end" for e in c.bus.history))
    assert call(httpd, "POST", "/api/session/register", {"speaker_id": "s1", "name": "小王"})[0] == 401
    status, body = call(httpd, "POST", "/api/session/register", {"speaker_id": "s1", "name": "小王"}, token)
    assert status == 409 and "账号" in body["error"]


def test_registering_from_the_page_saves_the_voice(web):
    httpd, c, tmp = web
    token = signed_up(httpd)
    call(httpd, "POST", "/api/session/start", {}, token)
    wait(lambda: sum(e["type"] == "speech_end" for e in c.bus.history) >= 6)
    status, body = call(httpd, "POST", "/api/session/register", {"speaker_id": "s1", "name": "小王"}, token)
    assert status == 200 and body["ok"] is True, body
    assert [s["name"] for s in call(httpd, "GET", "/api/speakers", token=token)[1]["speakers"]] == ["小王"]


def test_the_api_key_is_saved_privately_and_never_sent_back(web):
    httpd, _, tmp = web
    key = "AIzaSyDUMMYKEYDUMMYKEYDUMMYKEY123456"
    status, body = call(httpd, "POST", "/api/settings/key", {"key": key})
    assert status == 200 and body == {"has_key": True, "hint": "…3456"}
    cfg = tmp / "config" / "env"
    assert key in cfg.read_text() and stat.S_IMODE(os.stat(cfg).st_mode) == 0o600
    for path in ("/api/state", "/api/settings/key"):
        _, resp = call(httpd, "GET" if path == "/api/state" else "DELETE", path)
        assert key not in json.dumps(resp)
    assert call(httpd, "GET", "/api/state")[1]["has_key"] is False  # 上面的 DELETE 已经删掉了


def test_a_bad_key_is_rejected_and_other_settings_survive(web):
    httpd, _, tmp = web
    cfg = tmp / "config" / "env"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("OTHER=1\n")
    assert call(httpd, "POST", "/api/settings/key", {"key": "short"})[0] == 400
    call(httpd, "POST", "/api/settings/key", {"key": "AIzaSyDUMMYKEYDUMMYKEYDUMMYKEY123456"})
    call(httpd, "POST", "/api/settings/key", {"key": "AIzaSyANOTHERKEYANOTHERKEYANOTHER99"})  # 替换，不重复
    lines = cfg.read_text().splitlines()
    assert lines == ["OTHER=1", "GEMINI_API_KEY=AIzaSyANOTHERKEYANOTHERKEYANOTHER99"]


def test_session_endpoints_are_unavailable_without_a_controller(tmp_path):
    httpd = webapi.serve(0, AccountStore(tmp_path, kdf_cost=FAST))
    try:
        assert call(httpd, "POST", "/api/session/start", {})[0] == 404
        assert call(httpd, "GET", "/api/state")[1]["state"] == "unavailable"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_no_arguments_starts_the_web_app(monkeypatch):
    from givingear import app, cli

    seen = {}

    async def fake_run_app(web_port, ws_port, open_browser):
        seen.update(web_port=web_port, ws_port=ws_port, open_browser=open_browser)

    monkeypatch.setattr(app, "run_app", fake_run_app)
    cli.main([])
    assert seen == {"web_port": 8766, "ws_port": 8765, "open_browser": True}
    cli.main(["web", "--no-browser", "--web-port", "9000"])
    assert seen["web_port"] == 9000 and seen["open_browser"] is False


def test_terminal_options_still_mean_run(monkeypatch):
    from givingear import cli

    seen = {}

    async def fake_run(args):
        seen["wav"] = args.wav

    monkeypatch.setattr(cli, "_run", fake_run)
    cli.main(["--wav", "x.wav", "--fake"])
    assert seen["wav"] == "x.wav"


# ---- 选择模型 ----


def test_state_lists_the_providers_without_any_full_key(web):
    httpd, _, _ = web
    key = "sk-" + "d" * 30
    call(httpd, "POST", "/api/settings/key", {"provider": "deepseek", "key": key})
    state = call(httpd, "GET", "/api/state")[1]
    by_id = {p["id"]: p for p in state["providers"]}
    assert list(by_id) == ["gemini", "deepseek", "custom"]
    assert by_id["deepseek"]["has_key"] is True and by_id["deepseek"]["hint"] == "…dddd" and by_id["gemini"]["has_key"] is False
    assert by_id["deepseek"]["kind"] == "chat" and by_id["gemini"]["kind"] == "live"
    assert key not in json.dumps(state)


def test_keys_are_saved_per_provider(web):
    httpd, _, tmp = web
    call(httpd, "POST", "/api/settings/key", {"provider": "gemini", "key": "AIza" + "g" * 30})
    call(httpd, "POST", "/api/settings/key", {"provider": "deepseek", "key": "sk-" + "d" * 30})
    assert call(httpd, "DELETE", "/api/settings/key?provider=deepseek")[1]["has_key"] is False
    by_id = {p["id"]: p for p in call(httpd, "GET", "/api/state")[1]["providers"]}
    assert by_id["gemini"]["has_key"] is True and by_id["deepseek"]["has_key"] is False
    assert call(httpd, "POST", "/api/settings/key", {"provider": "nope", "key": "x" * 30})[0] == 400


def test_choosing_a_provider_is_saved_and_validated(web):
    httpd, _, _ = web
    s, b = call(httpd, "POST", "/api/settings/provider", {"provider": "deepseek", "zh_voice": "Meijia"})
    assert s == 200 and b["provider"] == "deepseek" and b["settings"]["zh_voice"] == "Meijia"
    assert call(httpd, "GET", "/api/state")[1]["provider"] == "deepseek"
    assert call(httpd, "POST", "/api/settings/provider", {"provider": "nope"})[0] == 400
    s, b = call(httpd, "POST", "/api/settings/provider", {"provider": "custom"})
    assert s == 400 and "接口地址和模型名" in b["error"]
    s, b = call(httpd, "POST", "/api/settings/provider", {"provider": "custom", "custom_base_url": "https://x/v1", "custom_model": "qwen-plus"})
    assert s == 200 and b["settings"]["custom_model"] == "qwen-plus"
    assert call(httpd, "POST", "/api/settings/provider", {"provider": 5})[0] == 400


def test_testing_the_connection_explains_success_and_failure(web, monkeypatch):
    from givingear.openai_client import ChatError

    httpd, _, _ = web
    call(httpd, "POST", "/api/settings/provider", {"provider": "deepseek"})
    assert call(httpd, "POST", "/api/settings/test", {})[1] == {"ok": False, "message": "还没有设置「DeepSeek（文字模型）」的密钥"}
    call(httpd, "POST", "/api/settings/key", {"provider": "deepseek", "key": "sk-" + "d" * 30})

    class Fake:
        def __init__(self, error=None):
            self.error = error

        async def ping(self):
            if self.error:
                raise self.error
            return "好"

        async def aclose(self):
            pass

    app = httpd.app
    app.chat_client_factory = lambda s: Fake()
    assert call(httpd, "POST", "/api/settings/test", {})[1] == {"ok": True, "message": "连接成功，模型回复：好"}
    app.chat_client_factory = lambda s: Fake(ChatError("密钥不对或已失效（HTTP 401）"))
    assert call(httpd, "POST", "/api/settings/test", {})[1] == {"ok": False, "message": "密钥不对或已失效（HTTP 401）"}


def test_starting_without_the_chosen_providers_key_says_which_one(web):
    httpd, _, _ = web
    # web 夹具用假后端，这里只检查真实后端的提示是否会原样传到页面
    from givingear.app import real_backend

    call(httpd, "POST", "/api/settings/provider", {"provider": "deepseek"})
    with pytest.raises(AppError, match="DeepSeek"):
        real_backend(None)


async def test_a_model_download_failure_reaches_the_page_and_frees_the_microphone(conversation, monkeypatch):
    from givingear import app as app_module
    from givingear.models import ModelDownloadError

    def fail(*a, **k):
        raise ModelDownloadError("下载模型 x 失败：连接超时\n  可以设置 GIVINGEAR_DOWNLOAD_MIRROR")

    monkeypatch.setattr(app_module, "Session", fail)
    c = make_controller(conversation.path)
    with pytest.raises(AppError, match="GIVINGEAR_DOWNLOAD_MIRROR"):
        await c.start()
    assert c.state == "idle" and c.closed == [True]


# ---- 端口被占用 ----


def occupy():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


async def test_a_busy_port_from_another_program_gives_a_clear_message():
    from givingear.app import run_app
    from givingear.config import ConfigError

    s, port = occupy()
    try:
        with pytest.raises(ConfigError, match=f"端口 {port} 已经被别的程序占用"):
            await run_app(0, port, open_browser=False)
    finally:
        s.close()


async def test_if_givingear_is_already_running_its_page_is_opened_instead(tmp_path, monkeypatch):
    from givingear import app as app_module

    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    running = webapi.serve(0, AccountStore(tmp_path, kdf_cost=FAST), controller=Controller(EventBus(), None, None), loop=asyncio.get_running_loop(), ws_port=1)
    web_port = running.server_address[1]
    s, ws_port = occupy()  # 事件流端口被占（因为已经有一个 GivingEar 在跑）
    try:
        await app_module.run_app(web_port, ws_port, open_browser=True)
    finally:
        s.close()
        running.shutdown()
        running.server_close()
    assert opened == [f"http://127.0.0.1:{web_port}/"]


async def test_the_web_port_being_busy_does_not_leave_the_event_port_open():
    from givingear.app import run_app
    from givingear.config import ConfigError

    s, web_port = occupy()
    try:
        with pytest.raises(ConfigError, match=f"端口 {web_port}"):
            await run_app(web_port, 0, open_browser=False)
    finally:
        s.close()


# ---- 语音引擎与试听 ----


def test_state_has_the_emotions_and_engines_and_the_voice_key_status(web):
    httpd, _, _ = web
    state = call(httpd, "GET", "/api/state")[1]
    assert [e["id"] for e in state["tts_engines"]] == ["mac", "qwen", "cosyvoice"]
    assert state["emotions"][:3] == ["平静", "开心", "兴奋"] and state["tts_key"] == {"has_key": False, "hint": ""}
    assert state["settings"]["tts_engine"] == "mac" and state["settings"]["qwen_voice"] == "Cherry"
    key = "sk-" + "q" * 30
    assert call(httpd, "POST", "/api/settings/key", {"provider": "dashscope", "key": key})[1] == {"has_key": True, "hint": "…qqqq"}
    state = call(httpd, "GET", "/api/state")[1]
    assert state["tts_key"]["hint"] == "…qqqq" and key not in json.dumps(state)
    assert {p["id"]: p["has_key"] for p in state["providers"]}["deepseek"] is False  # 和文字模型的密钥分开


def test_voice_settings_are_saved_and_validated(web):
    httpd, _, _ = web
    s, b = call(httpd, "POST", "/api/settings/provider", {"tts_engine": "cosyvoice", "cosyvoice_speaker": "/me.wav", "cosyvoice_rate": "22050"})
    assert s == 200 and b["settings"]["tts_engine"] == "cosyvoice" and b["settings"]["cosyvoice_rate"] == "22050"
    assert call(httpd, "POST", "/api/settings/provider", {"tts_engine": "nope"})[0] == 400
    assert call(httpd, "POST", "/api/settings/provider", {"cosyvoice_rate": "x"})[0] == 400


def test_auditioning_a_voice_returns_playable_audio_or_a_clear_reason(web):
    import base64
    import io
    import wave

    from givingear.tts import SynthError

    httpd, _, _ = web
    heard = []

    class Fake:
        async def synth(self, text, emotion=None):
            heard.append((text, emotion))
            return b"\x01\x00" * 2400, 24000

    httpd.app.synth_factory = lambda s: Fake()
    s, b = call(httpd, "POST", "/api/settings/test-voice", {"emotion": "开心", "text": "太棒了！"})
    assert s == 200 and b["ok"] is True and heard == [("太棒了！", "开心")]
    with wave.open(io.BytesIO(base64.b64decode(b["wav"]))) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 2400)
    assert call(httpd, "POST", "/api/settings/test-voice", {})[1]["ok"] is True and heard[-1][0] == "你好呀，今天过得怎么样？"
    assert call(httpd, "POST", "/api/settings/test-voice", {"emotion": "疯狂"})[0] == 400

    class Broken:
        async def synth(self, text, emotion=None):
            raise SynthError("连不上本地 CosyVoice 服务（http://127.0.0.1:50000）。请确认它已经启动")

    httpd.app.synth_factory = lambda s: Broken()
    assert call(httpd, "POST", "/api/settings/test-voice", {"emotion": "难过"})[1] == {
        "ok": False, "message": "连不上本地 CosyVoice 服务（http://127.0.0.1:50000）。请确认它已经启动"}


def test_the_page_and_the_backend_agree_on_the_api_version(web):
    """页面是每次从磁盘读的，后台可能是旧进程：两边版本号必须能对上，对不上页面会提示重启。"""
    import re

    httpd, _, _ = web
    html = (webapi.WEB_DIR / "index.html").read_text()
    expected = int(re.search(r"const EXPECTED_API = (\d+)", html).group(1))
    assert expected == webapi.API_VERSION == call(httpd, "GET", "/api/state")[1]["api_version"]


def test_the_settings_button_still_opens_the_dialog_when_loading_it_fails():
    import re

    html = (webapi.WEB_DIR / "index.html").read_text()
    handler = re.search(r'\$\("settings"\)\.onclick = \(\) => \{(.*?)\n\};', html, re.S).group(1)
    assert "try { fillSettings(); }" in handler and "showModal()" in handler
