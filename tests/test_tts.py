import shutil
import tempfile
from pathlib import Path

import pytest

from givingear.tts import MacSayTTS, SynthError

needs_mac = pytest.mark.skipif(not shutil.which("say"), reason="需要 macOS 的 say 命令")


def test_the_voice_follows_the_language():
    tts = MacSayTTS()
    assert tts.voice_for("你好，今天天气不错") == "Tingting"
    assert tts.voice_for("Hello there") == "Samantha"
    assert tts.voice_for("用 Python 写") == "Tingting"  # 夹着英文的中文句子仍按中文


@needs_mac
async def test_a_chinese_sentence_becomes_audio():
    pcm, rate = await MacSayTTS().synth("你好，我是 AI。")
    seconds = len(pcm) / 2 / rate
    assert rate == 24000 and 0.5 < seconds < 8


@needs_mac
async def test_empty_text_is_silent_and_leaves_no_files_behind():
    before = set(Path(tempfile.gettempdir()).iterdir())
    assert await MacSayTTS().synth("   ") == (b"", 24000)
    await MacSayTTS().synth("好")
    leftovers = {p for p in set(Path(tempfile.gettempdir()).iterdir()) - before if p.suffix in (".aiff", ".wav")}
    assert not leftovers


async def test_a_missing_voice_tool_gives_a_clear_error(monkeypatch):
    import givingear.tts as tts

    real = tts.asyncio.create_subprocess_exec

    async def fake(*cmd, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(tts.asyncio, "create_subprocess_exec", fake)
    with pytest.raises(SynthError, match="只支持 macOS"):
        await MacSayTTS().synth("你好")


def test_each_emotion_maps_to_prosody_commands():
    from givingear.emotions import EMOTIONS
    from givingear.tts import PROSODY, clean_for_say, prosody_prefix

    assert set(PROSODY) == set(EMOTIONS)  # 每种情绪都有对应的语速/音高设置
    assert prosody_prefix(None) == prosody_prefix("平静") == ""
    assert prosody_prefix("开心") == "[[pbas +10]][[rate +40]]"
    assert prosody_prefix("难过") == "[[pbas -12]][[rate -45]][[volm 0.65]]"
    assert prosody_prefix("不存在") == ""


def test_embedded_say_commands_in_the_text_are_removed():
    from givingear.tts import clean_for_say

    assert clean_for_say("你好[[rate +300]]呀 [[volm 5]]") == "你好rate +300呀 volm 5"
    assert "[[" not in clean_for_say("[[pbas +50]]你好")


@needs_mac
async def test_a_sad_sentence_is_slower_and_a_happy_one_is_faster():
    tts = MacSayTTS()
    text = "今天真是太开心了，我们一起去玩吧。"
    plain, happy, sad = [len((await tts.synth(text, e))[0]) for e in (None, "开心", "难过")]
    assert happy < plain < sad  # 开心更快、难过更慢
