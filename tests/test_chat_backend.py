"""文字模型后端：用假的文字模型和假的语音合成，只断言对外可见的行为（事件、发给模型的消息）。"""

import asyncio
import json

from givingear.audio import WavFileSource
from givingear.backend import BackendEvent, FakeBackend
from givingear.chat_backend import TextChatBackend, split_sentences
from givingear.events import EventBus
from givingear.session import Session
from givingear.prompts import system_prompt
from givingear.tools import NAME_SPEAKER


class FakeChat:
    """按顺序返回预先写好的回复。每条回复是一个列表：字符串是文字片段，("tool", id, 名字, 参数) 是工具调用。"""

    def __init__(self, replies, delay=0.0):
        self.replies, self.calls, self.delay = list(replies), [], delay

    async def chat(self, messages, tools):
        self.calls.append(json.loads(json.dumps(messages)))
        if self.delay:
            await asyncio.sleep(self.delay)
        reply = self.replies.pop(0) if self.replies else ["[沉默]"]
        if isinstance(reply, Exception):
            raise reply
        for item in reply:
            if isinstance(item, str):
                yield ("text", item)
                await asyncio.sleep(0)
            else:
                yield ("tool_call", *item[1:])


class FakeSynth:
    """每句话合成 0.1 秒的静音，并记下合成了什么。"""

    def __init__(self):
        self.spoken, self.emotions = [], []

    async def synth(self, text, emotion=None):
        self.spoken.append(text)
        self.emotions.append(emotion)
        return b"\0\0" * 2400, 24000


def make(replies, **kw):
    chat, synth = FakeChat(replies, kw.pop("delay", 0.0)), FakeSynth()
    b = TextChatBackend(chat, synth, "SYSTEM", [NAME_SPEAKER], reply_delay=kw.pop("reply_delay", 0.01), **kw)
    return b, chat, synth


async def drain(b, until_types=("ai_turn_end",), timeout=5):
    got = []
    agen = b.events()
    try:
        while True:
            e = await asyncio.wait_for(agen.__anext__(), timeout)
            got.append(e)
            if e.kind in until_types:
                return got
    finally:
        await agen.aclose()


def kinds(events):
    return [e.kind for e in events]


def test_sentences_are_split_as_they_complete():
    assert split_sentences("你好。我是 AI，很高") == (["你好。"], "我是 AI，很高")
    assert split_sentences("Hello world. How are") == (["Hello world."], " How are")
    text = "你好。Hello world. 3.5度！还有"
    done, tail = split_sentences(text)
    assert "".join(done) + tail == text  # 切开后一个字都不丢
    assert split_sentences("温度是3.5度。") == (["温度是3.5度。"], "")
    assert split_sentences("没写完") == ([], "没写完")


async def test_a_transcript_gets_a_spoken_reply_sentence_by_sentence():
    b, chat, synth = make([["你好，小王。", "今天想聊", "什么？"]])
    await b.send_transcript("s1", "小王", "你好", 1.0)
    events = await drain(b)
    assert chat.calls[0][0] == {"role": "system", "content": "SYSTEM"}
    assert chat.calls[0][-1] == {"role": "user", "content": "[小王]：你好"}
    assert [e.text for e in events if e.kind == "ai_text"] == ["你好，小王。", "今天想聊什么？"]
    assert synth.spoken == ["你好，小王。", "今天想聊什么？"]
    assert kinds(events) == ["ai_text", "ai_audio", "ai_text", "ai_audio", "ai_turn_end"]
    assert all(e.rate == 24000 and e.audio for e in events if e.kind == "ai_audio")


async def test_a_silent_reply_is_not_spoken_but_remembered():
    b, chat, synth = make([["[沉默]"], ["好的。"]])
    await b.send_transcript("s1", "小王", "小李，周末去爬山吗", 1.0)
    await asyncio.sleep(0.2)
    assert synth.spoken == [] and b._out.empty()
    await b.send_transcript("s2", "小李", "好啊", 2.0)
    await drain(b)
    assert {"role": "assistant", "content": "[沉默]"} in chat.calls[1]  # 上一轮的沉默还在上下文里


async def test_a_silence_marker_split_across_chunks_is_still_silence():
    b, _, synth = make([["[沉", "默]"]])
    await b.send_transcript("s1", "小王", "你们聊", 1.0)
    await asyncio.sleep(0.2)
    assert synth.spoken == [] and b._out.empty()


async def test_quick_back_to_back_utterances_trigger_one_reply():
    b, chat, _ = make([["收到。"]], reply_delay=0.15)
    await b.send_transcript("s1", "小王", "我想", 1.0)
    await asyncio.sleep(0.05)
    await b.send_transcript("s1", "小王", "去爬山", 1.5)
    await drain(b)
    assert len(chat.calls) == 1
    assert [m["content"] for m in chat.calls[0][-2:]] == ["[小王]：我想", "[小王]：去爬山"]


async def test_a_tool_call_is_reported_and_the_model_continues_after_the_result():
    b, chat, synth = make([[("tool", "c1", "name_speaker", {"name": "小王"})], ["好的，小王。"]])
    await b.send_transcript("s1", "说话人 1", "我叫小王", 1.0)
    events = await drain(b, until_types=("tool_call",))
    call = events[-1]
    assert (call.tool, call.args, call.call_id) == ("name_speaker", {"name": "小王"}, "c1")
    await b.send_tool_result("c1", "name_speaker", {"ok": True})
    events = await drain(b)
    assert [e.text for e in events if e.kind == "ai_text"] == ["好的，小王。"]
    roles = [m["role"] for m in chat.calls[1]]
    assert roles[-3:] == ["user", "assistant", "tool"]
    assert chat.calls[1][-2]["tool_calls"][0]["function"] == {"name": "name_speaker", "arguments": '{"name": "小王"}'}


async def test_notes_that_arrive_while_waiting_for_a_tool_result_do_not_break_the_order():
    b, chat, _ = make([[("tool", "c1", "name_speaker", {"name": "小王"})], ["好的。"]])
    await b.send_transcript("s1", "说话人 1", "我叫小王", 1.0)
    await drain(b, until_types=("tool_call",))
    await b.send_note("[房间里的人：小王]")  # 工具结果之前插进来的名单
    await b.send_tool_result("c1", "name_speaker", {"ok": True})
    await drain(b)
    roles = [m["role"] for m in chat.calls[1]]
    assert roles[-3:] == ["assistant", "tool", "user"]
    assert chat.calls[1][-1]["content"] == "[房间里的人：小王]"


async def test_interrupting_cancels_the_reply_and_stops_playback():
    b, chat, synth = make([["第一句。", "第二句。"]], delay=0.3)
    await b.send_transcript("s1", "小王", "讲个故事", 1.0)
    await asyncio.sleep(0.1)  # 回复已经开始生成，但还没出来
    await b.interrupt()
    assert (await drain(b, until_types=("interrupted",)))[-1].kind == "interrupted"
    await asyncio.sleep(0.4)
    assert synth.spoken == [] and b._out.empty()


async def test_interrupting_while_audio_is_still_playing_reports_it():
    b, _, _ = make([["一句话。"]])
    await b.send_transcript("s1", "小王", "你好", 1.0)
    await drain(b)  # 语音已经发出去了（约 0.1 秒），但还在"播放"
    await b.interrupt()
    assert (await drain(b, until_types=("interrupted",)))[-1].kind == "interrupted"


async def test_interrupting_when_nothing_is_happening_is_silent():
    b, _, _ = make([])
    await b.interrupt()
    assert b._out.empty()


async def test_pending_reply_is_dropped_if_someone_keeps_talking():
    b, chat, _ = make([["不该说。"]], reply_delay=0.3)
    await b.send_transcript("s1", "小王", "我想想", 1.0)
    await asyncio.sleep(0.05)
    await b.interrupt()  # 小王又开口了
    await asyncio.sleep(0.4)
    assert chat.calls == []


async def test_a_model_error_is_reported_and_the_next_utterance_still_works():
    b, _, _ = make([RuntimeError("网络不通"), ["恢复了。"]])
    await b.send_transcript("s1", "小王", "你好", 1.0)
    assert "网络不通" in (await drain(b, until_types=("error",)))[-1].text
    await b.send_transcript("s1", "小王", "还在吗", 2.0)
    assert [e.text for e in await drain(b) if e.kind == "ai_text"] == ["恢复了。"]


async def test_history_is_trimmed_from_the_oldest_and_never_starts_with_a_tool_result():
    b, chat, _ = make([["[沉默]"]] * 6, max_history=4)
    for i in range(6):
        await b.send_transcript("s1", "小王", f"第{i}句", float(i))
        await asyncio.sleep(0.1)
    last = chat.calls[-1]
    assert len(last) <= 1 + 4 and last[1]["role"] == "user"
    assert last[-1]["content"] == "[小王]：第5句"


async def test_the_session_feeds_transcripts_and_interrupts_to_the_backend(conversation):
    backend = FakeBackend()
    await Session(backend, EventBus()).run(WavFileSource(conversation.path))
    assert len(backend.transcripts) == 6 and backend.transcripts[0][1] == "说话人 1"
    assert backend.interrupts == 6  # 每次有人开口都通知一次


async def test_the_whole_pipeline_runs_inside_a_session(conversation):
    from test_app import HoldOpen

    chat, synth = FakeChat([["[沉默]"]] * 5 + [["好的。"]]), FakeSynth()
    backend = TextChatBackend(chat, synth, "SYSTEM", [NAME_SPEAKER], reply_delay=0.05)
    bus = EventBus()
    task = asyncio.create_task(Session(backend, bus).run(HoldOpen(WavFileSource(conversation.path))))
    for _ in range(600):
        users_now = [m["content"] for m in chat.calls[-1] if m["role"] == "user"] if chat.calls else []
        if any("[说话人 1]" in u for u in users_now) and any("[说话人 2]" in u for u in users_now):
            break
        await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    users = [m["content"] for m in chat.calls[-1] if m["role"] == "user"]
    assert any(u.startswith("[说话人 1]：") for u in users) and any(u.startswith("[说话人 2]：") for u in users)


# ---- 情绪 ----


async def test_emotion_tags_are_stripped_and_passed_to_the_voice():
    b, _, synth = make([["[开心]你好呀！", "【温柔】", "慢慢来。"]])
    await b.send_transcript("s1", "小王", "我考过了", 1.0)
    events = await drain(b)
    assert [e.text for e in events if e.kind == "ai_text"] == ["你好呀！", "慢慢来。"]  # 页面上和朗读都没有标签
    assert synth.spoken == ["你好呀！", "慢慢来。"] and synth.emotions == ["开心", "温柔"]


async def test_a_sentence_without_a_tag_keeps_the_previous_emotion_of_this_reply():
    b, _, synth = make([["[难过]真遗憾。", "我陪着你。"], ["没有标签。"]])
    await b.send_transcript("s1", "小王", "我没考过", 1.0)
    await drain(b)
    assert synth.emotions == ["难过", "难过"]
    await b.send_transcript("s1", "小王", "谢谢", 2.0)
    await drain(b)
    assert synth.emotions[-1] is None  # 新的一轮不会沿用上一轮的情绪


async def test_brackets_that_are_not_emotions_are_left_alone():
    b, _, synth = make([["[小王]你好。"]])
    await b.send_transcript("s1", "小王", "嗨", 1.0)
    await drain(b)
    assert synth.spoken == ["[小王]你好。"] and synth.emotions == [None]


async def test_silence_and_emotion_tags_do_not_interfere():
    b, _, synth = make([["[沉默]"], ["[开", "心]好的。"]])
    await b.send_transcript("s1", "小王", "你们聊", 1.0)
    await asyncio.sleep(0.2)
    assert synth.spoken == []
    await b.send_transcript("s2", "小李", "AI 你觉得呢", 2.0)
    await drain(b)
    assert synth.spoken == ["好的。"] and synth.emotions == ["开心"]


def test_emotion_parsing():
    from givingear.emotions import EMOTIONS, instruction, parse_emotion

    assert parse_emotion("[开心]好呀") == ("开心", "好呀")
    assert parse_emotion(" （关切） 怎么了") == ("关切", "怎么了")
    assert parse_emotion("没有标签") == (None, "没有标签")
    assert parse_emotion("[开心") == (None, "[开心")
    assert instruction("难过") == EMOTIONS["难过"] and instruction(None) == EMOTIONS["平静"] and instruction("不存在") == EMOTIONS["平静"]


def test_the_prompt_asks_for_emotions_only_when_the_voice_can_use_them():
    from givingear.emotions import EMOTIONS

    plain, rich = system_prompt(False, text_mode=True), system_prompt(False, text_mode=True, emotional=True)
    assert "情绪标签" not in plain and "情绪标签" in rich
    assert all(f"[{e}]" in rich for e in EMOTIONS) and "语气词" in rich
    assert "[沉默]" in rich
