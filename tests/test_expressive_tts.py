"""有情感的语音引擎：用假服务器验证请求的写法和对返回内容的处理。"""

import io
import json
import wave

import httpx
import pytest

from givingear import config, providers
from givingear.app import AppError, real_backend
from givingear.config import ConfigError
from givingear.emotions import EMOTIONS
from givingear.tts import SynthError
from givingear.tts_expressive import CosyVoiceTTS, QwenCloudTTS, decode_wav
from givingear.voices import make_synthesizer


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GIVINGEAR_CONFIG", str(tmp_path / "cfg" / "env"))
    for name in ("GEMINI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def wav_bytes(seconds=0.2, rate=24000, channels=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds) * channels)
    return buf.getvalue()


def qwen(handler, **kw):
    return QwenCloudTTS("sk-test", transport=httpx.MockTransport(handler), **kw)


async def test_qwen_sends_the_emotion_as_an_instruction_and_fetches_the_audio():
    seen = []

    def handler(request):
        if request.url.host == "dashscope.aliyuncs.com":
            seen.append((str(request.url), request.headers["authorization"], json.loads(request.content)))
            return httpx.Response(200, json={"output": {"audio": {"url": "https://oss.example.com/a.wav"}}})
        return httpx.Response(200, content=wav_bytes(0.5))

    pcm, rate = await qwen(handler).synth("你好呀", "开心")
    url, auth, body = seen[0]
    assert url == "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation" and auth == "Bearer sk-test"
    assert body["model"] == "qwen3-tts-instruct-flash"
    assert body["input"] == {"text": "你好呀", "voice": "Cherry", "instructions": EMOTIONS["开心"], "optimize_instructions": True}
    assert rate == 24000 and len(pcm) == 24000 // 2 * 2


async def test_qwen_models_without_instruction_support_get_no_instructions():
    bodies = []

    def handler(request):
        if request.url.host == "dashscope.aliyuncs.com":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"output": {"audio": {"url": "https://x/a.wav"}}})
        return httpx.Response(200, content=wav_bytes())

    await qwen(handler, model="qwen3-tts-flash").synth("你好", "开心")
    assert "instructions" not in bodies[0]["input"]


@pytest.mark.parametrize("status,expect", [(401, "密钥不对"), (429, "额度"), (500, "出错")])
async def test_qwen_errors_are_explained(status, expect):
    with pytest.raises(SynthError, match=expect):
        await qwen(lambda r: httpx.Response(status, text="boom")).synth("你好")


async def test_qwen_bad_responses_and_network_failures_have_clear_messages():
    with pytest.raises(SynthError, match="没有返回音频地址"):
        await qwen(lambda r: httpx.Response(200, json={"output": {}})).synth("你好")

    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(SynthError, match="连不上通义千问"):
        await qwen(boom).synth("你好")
    assert await qwen(boom).synth("   ") == (b"", 24000)  # 空文本不发请求


def test_wav_decoding():
    pcm, rate = decode_wav(wav_bytes(0.1, 16000))
    assert rate == 16000 and len(pcm) == 1600 * 2
    stereo, _ = decode_wav(wav_bytes(0.1, 16000, channels=2))
    assert len(stereo) == len(pcm)  # 多声道取第一个声道
    with pytest.raises(SynthError, match="不是 WAV"):
        decode_wav(b"ID3 mp3 data")


async def test_cosyvoice_instruct2_uploads_the_reference_voice(tmp_path):
    ref = tmp_path / "me.wav"
    ref.write_bytes(wav_bytes())
    seen = {}

    def handler(request):
        seen["path"], seen["body"] = request.url.path, request.content
        return httpx.Response(200, content=b"\x01\x00" * 100 + b"\x02")  # 多出的 1 个字节会被丢掉

    tts = CosyVoiceTTS("http://127.0.0.1:50000/", "instruct2", str(ref), 24000, transport=httpx.MockTransport(handler))
    pcm, rate = await tts.synth("你好", "温柔")
    assert seen["path"] == "/inference_instruct2" and len(pcm) == 200 and rate == 24000
    body = seen["body"].decode("latin1")
    assert "tts_text" in body and EMOTIONS["温柔"] in seen["body"].decode("utf8", "ignore") and 'name="prompt_wav"' in body


async def test_cosyvoice_instruct_uses_a_preset_speaker():
    seen = {}

    def handler(request):
        seen["path"], seen["body"] = request.url.path, request.content.decode()
        return httpx.Response(200, content=b"\x00\x00" * 10)

    tts = CosyVoiceTTS(mode="instruct", speaker="中文女", sample_rate=22050, transport=httpx.MockTransport(handler))
    pcm, rate = await tts.synth("你好", "难过")
    assert seen["path"] == "/inference_instruct" and "spk_id=" in seen["body"] and rate == 22050


async def test_cosyvoice_problems_are_explained(tmp_path):
    with pytest.raises(SynthError, match="参考录音"):
        await CosyVoiceTTS(mode="instruct2", speaker="").synth("你好")
    with pytest.raises(SynthError, match="参考录音"):
        await CosyVoiceTTS(mode="instruct2", speaker=str(tmp_path / "missing.wav")).synth("你好")

    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(SynthError, match="已经启动"):
        await CosyVoiceTTS(mode="instruct", transport=httpx.MockTransport(boom)).synth("你好")
    with pytest.raises(SynthError, match="出错"):
        await CosyVoiceTTS(mode="instruct", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="x"))).synth("你好")


# ---- 设置与选择 ----


def test_settings_for_the_voice_are_validated():
    ok = providers.Settings(tts_engine="qwen", cosyvoice_rate="22050")
    providers.save_settings(ok)
    assert providers.load_settings() == ok
    for bad, match in [({"tts_engine": "nope"}, "语音引擎"), ({"cosyvoice_mode": "x"}, "instruct"), ({"cosyvoice_rate": "abc"}, "采样率"),
                       ({"cosyvoice_rate": "99999"}, "采样率"), ({"qwen_base_url": "ftp://x"}, "http")]:
        with pytest.raises(ConfigError, match=match):
            providers.save_settings(providers.Settings(**bad))


def test_the_engine_follows_the_setting_and_says_whether_it_supports_emotion():
    from givingear.tts import MacSayTTS

    synth, emotional = make_synthesizer(providers.Settings())
    assert isinstance(synth, MacSayTTS) and emotional is True  # 本机声音也会按情绪调语速和音高
    synth, emotional = make_synthesizer(providers.Settings(tts_engine="cosyvoice"))
    assert isinstance(synth, CosyVoiceTTS) and emotional is True
    with pytest.raises(ConfigError, match="DashScope"):
        make_synthesizer(providers.Settings(tts_engine="qwen"))
    config.save_api_key("DASHSCOPE_API_KEY", "sk-" + "q" * 30)
    synth, emotional = make_synthesizer(providers.Settings(tts_engine="qwen", qwen_voice="Serena"))
    assert isinstance(synth, QwenCloudTTS) and synth.voice == "Serena" and emotional is True


def test_an_expressive_engine_makes_the_model_tag_emotions():
    providers.save_settings(providers.Settings(provider="deepseek", tts_engine="cosyvoice"))
    config.save_api_key("DEEPSEEK_API_KEY", "sk-" + "d" * 30)
    assert "情绪标签" in real_backend(None)._system
    providers.save_settings(providers.Settings(provider="deepseek"))
    assert "情绪标签" in real_backend(None)._system  # 本机自带的声音也能用情绪调语速和音高


def test_a_missing_dashscope_key_is_reported_as_an_app_error():
    providers.save_settings(providers.Settings(provider="deepseek", tts_engine="qwen"))
    config.save_api_key("DEEPSEEK_API_KEY", "sk-" + "d" * 30)
    with pytest.raises(AppError, match="DashScope"):
        real_backend(None)
