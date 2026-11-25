"""模型提供商：OpenAI 兼容客户端、各提供商的密钥和设置、按设置选择后端。"""

import json
import os
import stat

import httpx
import pytest

from givingear import config, providers
from givingear.app import AppError, real_backend
from givingear.chat_backend import TextChatBackend
from givingear.config import ConfigError
from givingear.openai_client import ChatError, OpenAIChatClient
from givingear.prompts import system_prompt
from givingear.tools import NAME_SPEAKER


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GIVINGEAR_CONFIG", str(tmp_path / "cfg" / "env"))
    for name in ("GEMINI_API_KEY", "DEEPSEEK_API_KEY", "CUSTOM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def sse(*chunks) -> str:
    lines = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks]
    return ": keep-alive\n\n" + "".join(lines) + "data: [DONE]\n\n"


def text(s):
    return {"choices": [{"delta": {"content": s}}]}


def client_for(handler, **kw):
    return OpenAIChatClient("https://api.example.com/v1/", "sk-test", "m1", transport=httpx.MockTransport(handler), **kw)


async def collect(client, messages=None, tools=()):
    return [x async for x in client.chat(messages or [{"role": "user", "content": "hi"}], list(tools))]


async def test_text_arrives_in_chunks_and_the_request_is_well_formed():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, text=sse(text("你"), {"choices": [{"delta": {}}]}, text("好")), headers={"content-type": "text/event-stream"})

    out = await collect(client_for(handler), tools=[NAME_SPEAKER])
    assert out == [("text", "你"), ("text", "好")]
    assert seen["url"] == "https://api.example.com/v1/chat/completions" and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["stream"] is True and seen["body"]["model"] == "m1"
    assert seen["body"]["tools"][0]["function"]["name"] == "name_speaker"


async def test_tool_call_arguments_arrive_in_fragments_and_are_assembled():
    def tc(**kw):
        return {"choices": [{"delta": {"tool_calls": [{"index": 0, **kw}]}}]}

    body = sse(tc(id="call_1", function={"name": "name_speaker", "arguments": ""}),
               tc(function={"arguments": '{"name": "小'}), tc(function={"arguments": '王"}'}))
    out = await collect(client_for(lambda r: httpx.Response(200, text=body)))
    assert out == [("tool_call", "call_1", "name_speaker", {"name": "小王"})]


@pytest.mark.parametrize("status,expect", [(401, "密钥不对"), (402, "余额不足"), (404, "模型名不对"), (429, "太频繁")])
async def test_errors_are_explained_in_chinese(status, expect):
    with pytest.raises(ChatError, match=expect):
        await collect(client_for(lambda r: httpx.Response(status, json={"error": {"message": "x"}})))


async def test_a_network_failure_says_to_check_the_network():
    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(ChatError, match="连不上"):
        await collect(client_for(boom))
    with pytest.raises(ChatError, match="连不上"):
        await client_for(boom).ping()


async def test_ping_returns_the_reply_or_a_clear_error():
    ok = client_for(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": " 好 "}}]}))
    assert await ok.ping() == "好"
    with pytest.raises(ChatError, match="密钥不对"):
        await client_for(lambda r: httpx.Response(401, json={})).ping()
    with pytest.raises(ChatError, match="OpenAI 兼容"):
        await client_for(lambda r: httpx.Response(200, text="<html>")).ping()


# ---- 密钥与设置 ----


def test_each_providers_key_is_stored_separately_and_privately(isolated_config):
    config.save_api_key("DEEPSEEK_API_KEY", "sk-" + "d" * 30)
    config.save_gemini_api_key("AIza" + "g" * 30)
    config.save_api_key("DEEPSEEK_API_KEY", "sk-" + "e" * 30)  # 替换 DeepSeek 的，不碰 Gemini 的
    assert config.read_key("DEEPSEEK_API_KEY") == "sk-" + "e" * 30
    assert config.read_key("GEMINI_API_KEY") == "AIza" + "g" * 30
    assert stat.S_IMODE(os.stat(config.config_file()).st_mode) == 0o600
    config.delete_api_key("DEEPSEEK_API_KEY")
    assert config.key_status("DEEPSEEK_API_KEY") == {"has_key": False, "hint": ""}
    assert config.key_status("GEMINI_API_KEY")["hint"] == "…gggg"


def test_settings_roundtrip_and_validation():
    assert providers.load_settings().provider == "gemini"
    s = providers.Settings(provider="deepseek", zh_voice="Meijia")
    providers.save_settings(s)
    assert providers.load_settings() == s
    with pytest.raises(ConfigError, match="不支持"):
        providers.save_settings(providers.Settings(provider="nope"))
    with pytest.raises(ConfigError, match="http"):
        providers.save_settings(providers.Settings(provider="custom", custom_base_url="ftp://x"))


def test_a_damaged_settings_file_falls_back_to_defaults():
    providers.settings_file().parent.mkdir(parents=True)
    providers.settings_file().write_text("{ not json")
    assert providers.load_settings() == providers.Settings()
    providers.settings_file().write_text(json.dumps({"provider": "gone", "zh_voice": 5}))
    assert providers.load_settings().provider == "gemini"


def test_endpoints():
    s = providers.Settings(provider="deepseek")
    assert s.endpoint() == ("https://api.deepseek.com", "deepseek-chat")
    with pytest.raises(ConfigError, match="接口地址和模型名"):
        providers.Settings(provider="custom").endpoint()
    assert providers.Settings(custom_base_url=" https://x/v1 ", custom_model="qwen").endpoint("custom") == ("https://x/v1", "qwen")


# ---- 按设置选择后端 ----


def test_choosing_deepseek_gives_the_text_pipeline():
    providers.save_settings(providers.Settings(provider="deepseek"))
    config.save_api_key("DEEPSEEK_API_KEY", "sk-" + "d" * 30)
    backend = real_backend(None)
    assert isinstance(backend, TextChatBackend)
    assert backend._client.url == "https://api.deepseek.com/chat/completions" and backend._client.model == "deepseek-chat"
    assert [t.name for t in backend._tools] == ["name_speaker"]
    assert "[沉默]" in backend._system


def test_logged_in_text_backend_can_register():
    providers.save_settings(providers.Settings(provider="deepseek"))
    config.save_api_key("DEEPSEEK_API_KEY", "sk-" + "d" * 30)

    class Vault:
        pass

    backend = real_backend(Vault())
    assert [t.name for t in backend._tools] == ["name_speaker", "register_speaker"]


def test_choosing_gemini_gives_the_live_backend():
    from givingear.gemini import GeminiBackend

    config.save_gemini_api_key("AIza" + "g" * 30)
    assert isinstance(real_backend(None), GeminiBackend)


def test_a_missing_key_names_the_chosen_provider():
    providers.save_settings(providers.Settings(provider="deepseek"))
    with pytest.raises(AppError, match="DeepSeek"):
        real_backend(None)


def test_custom_needs_an_address_and_model():
    providers.save_settings(providers.Settings(provider="custom"))
    config.save_api_key("CUSTOM_API_KEY", "k" * 30)
    with pytest.raises(AppError, match="接口地址和模型名"):
        real_backend(None)
    providers.save_settings(providers.Settings(provider="custom", custom_base_url="https://x/v1", custom_model="qwen-plus"))
    assert real_backend(None)._client.model == "qwen-plus"


def test_prompts_for_text_models():
    p = system_prompt(False, text_mode=True)
    assert "[沉默]" in p and "[小王]：你好" in p and "朗读" in p and "需要先登录账号" in p
    assert "register_speaker" in system_prompt(True, text_mode=True)
    assert "[沉默]" not in system_prompt(False)  # 语音版不变
