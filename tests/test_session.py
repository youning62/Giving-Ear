"""核心测试接口：WAV 输入 + 假后端，只断言对外可见的行为（事件流、后端收到的内容、硬盘上的文件）。"""

import asyncio
import io
import json
import pytest
from websockets.asyncio.client import connect

from givingear import cli, models
from givingear.cli import Renderer
from givingear.audio import WavFileSource
from givingear.backend import BackendEvent, FakeBackend
from givingear.events import EventBus
from givingear.server import start_server
from givingear.session import Session
from givingear.speakers import SessionProfiles


async def run_session(path, bus=None):
    bus = bus or EventBus()
    backend = FakeBackend()
    await Session(backend, bus).run(WavFileSource(path))
    bus.close()
    return bus.history, backend


def truth_speaker(turns, start, end):
    """和这段时间重叠最多的真实说话人。"""
    overlap = lambda t: min(end, t.end) - max(start, t.start)
    return max(turns, key=overlap).speaker


async def test_each_speaker_gets_one_stable_distinct_id(conversation):
    events, _ = await run_session(conversation.path)
    ends = [e for e in events if e["type"] == "speech_end"]
    assert ends, "应当检测到说话"
    ids_by_truth: dict[str, set[str]] = {}
    for e in ends:
        ids_by_truth.setdefault(truth_speaker(conversation.turns, e["start"], e["audio_t"]), set()).add(e["speaker_id"])
    assert set(ids_by_truth) == {"A", "B", "C"}
    assert all(len(ids) == 1 for ids in ids_by_truth.values()), ids_by_truth
    assert len({next(iter(ids)) for ids in ids_by_truth.values()}) == 3


async def test_new_voices_get_temporary_profiles_in_order(conversation):
    events, _ = await run_session(conversation.path)
    created = [e for e in events if e["type"] == "profile_created"]
    assert [e["name"] for e in created] == ["说话人 1", "说话人 2", "说话人 3"]
    assert not any(e["registered"] for e in created)


async def test_backend_gets_label_about_one_second_after_speaking_starts(conversation):
    _, backend = await run_session(conversation.path)
    assert backend.connected and backend.closed
    for turn in conversation.turns:
        labels = [l for l in backend.labels if abs(l.utterance_start - turn.start) < 0.5]
        assert labels, f"{turn} 没有收到标签"
        assert labels[0].audio_t - turn.start <= 1.5
        assert labels[0].text.startswith("[现在说话的是：")


async def test_labels_name_the_same_speaker_consistently(conversation):
    _, backend = await run_session(conversation.path)
    names_by_truth: dict[str, set[str]] = {}
    for l in backend.labels:
        names_by_truth.setdefault(truth_speaker(conversation.turns, l.utterance_start, l.audio_t), set()).add(l.name)
    assert all(len(n) == 1 for n in names_by_truth.values()), names_by_truth


async def test_never_writes_audio_to_disk(conversation, tmp_path, monkeypatch):
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("GIVINGEAR_MODELS_DIR", str(models.models_dir()))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    await run_session(conversation.path)
    written = [p for root in (home, work) for p in root.rglob("*") if p.is_file()]
    assert written == []


async def test_websocket_client_receives_the_event_stream(conversation, unused_tcp_port):
    bus = EventBus()
    server = await start_server(bus, port=unused_tcp_port)
    received = []

    async def client():
        async with connect(f"ws://127.0.0.1:{unused_tcp_port}") as ws:
            async for msg in ws:
                received.append(json.loads(msg))

    task = asyncio.create_task(client())
    await asyncio.sleep(0.2)  # 等客户端连上
    events, _ = await run_session(conversation.path, bus)
    await asyncio.wait_for(task, 5)
    server.close()
    await server.wait_closed()
    assert [e["type"] for e in received] == [e["type"] for e in events]
    assert received[-1]["type"] == "closed"
    guess = next(e for e in received if e["type"] == "speaker_guess")
    assert {"wall_t", "audio_t", "speaker_id", "name", "utterance_id"} <= guess.keys()


def test_cli_shows_speaker_labels(conversation, capsys):
    cli.main(["--wav", str(conversation.path), "--fake", "--port", "0"])
    out = capsys.readouterr().out
    assert "正在听" in out
    assert "[说话人 1] 开口" in out
    assert "[说话人 3]" in out
    assert "已停止" in out


async def test_errors_are_published_on_the_event_stream(conversation):
    class BrokenBackend(FakeBackend):
        async def send_audio(self, frame):
            raise ConnectionError("网络断开")

    bus = EventBus()
    with pytest.raises(ConnectionError):
        await Session(BrokenBackend(), bus).run(WavFileSource(conversation.path))
    error = next(e for e in bus.history if e["type"] == "error")
    assert "网络断开" in error["message"]
    last = bus.history[-1]
    assert (last["type"], last["state"]) == ("status", "stopped")


class FakeSink:
    def __init__(self):
        self.played = 0
        self.stops = 0

    def play(self, pcm16, rate):
        self.played += len(pcm16)

    def stop_playback(self):
        self.stops += 1


class EchoingBackend(FakeBackend):
    """每收到一个说话人标签，就模拟模型发回：这句话的转写、一段 AI 语音和文字，然后被打断。"""

    async def send_speaker_label(self, label):
        await super().send_speaker_label(label)
        for e in (
            BackendEvent("user_text", text=f"我是{label.name}"),
            BackendEvent("ai_audio", audio=b"\x00\x01" * 240),
            BackendEvent("ai_text", text="好的"),
            BackendEvent("interrupted"),
        ):
            self.emit(e)


async def test_model_replies_reach_the_event_stream_and_speaker(conversation):
    bus, sink, backend = EventBus(), FakeSink(), EchoingBackend()
    session = Session(backend, bus, sink=sink)
    await session.run(WavFileSource(conversation.path))
    assert any(e["type"] == "model_transcript" and e["text"].startswith("我是") for e in bus.history)
    assert sink.played > 0
    assert sink.stops == sum(e["type"] == "ai_interrupted" for e in bus.history) > 0
    assert any(e["type"] == "ai_speaking" for e in bus.history)


def test_renderer_shows_a_chat_transcript():
    out = io.StringIO()
    r = Renderer(out=out)
    for e in [
        {"type": "transcript", "name": "说话人 1", "text": "明天几点开会？"},
        {"type": "ai_text", "text": "上午"},
        {"type": "ai_text", "text": "十点。"},
        {"type": "model_transcript", "text": "明天几点"},  # 模型转写默认不显示
        {"type": "ai_turn_end"},
        {"type": "transcript", "name": "说话人 2", "text": "好的"},
    ]:
        r.handle(e)
    assert out.getvalue() == "[说话人 1]: 明天几点开会？\nAI: 上午十点。\n[说话人 2]: 好的\n"


def similarity(a: str, b: str) -> float:
    """两段文字共有字符的比例，忽略标点和大小写。"""
    clean = lambda s: [c for c in s.lower() if c.isalnum()]
    a, b = clean(a), clean(b)
    common = sum(min(a.count(c), b.count(c)) for c in set(a))
    return common / max(len(b), 1)


async def test_each_utterance_gets_a_local_transcript_with_its_speaker(conversation):
    from conftest import CONVERSATION

    events, _ = await run_session(conversation.path)
    transcripts = [e for e in events if e["type"] == "transcript"]
    ends = {e["utterance_id"]: e for e in events if e["type"] == "speech_end"}
    assert len(transcripts) == len(ends)
    for t in transcripts:
        assert t["speaker_id"] == ends[t["utterance_id"]]["speaker_id"]  # 文字归属到这句话的说话人
    for turn, (_, text) in zip(conversation.turns, CONVERSATION):
        t = max(transcripts, key=lambda e: min(e["audio_t"], turn.end) - max(e["start"], turn.start))
        assert similarity(t["text"], text) >= 0.6, (t["text"], text)


class NamingBackend(FakeBackend):
    """在某个说话人第一次出现时，模拟模型调用 name_speaker 工具。"""

    def __init__(self, when_speaker: str, args: dict):
        super().__init__()
        self.when_speaker, self.args, self.fired = when_speaker, args, False

    async def send_speaker_label(self, label):
        await super().send_speaker_label(label)
        if label.speaker_id == self.when_speaker and not self.fired:
            self.fired = True
            self.emit(BackendEvent("tool_call", tool="name_speaker", args=self.args, call_id="c1"))


async def test_naming_the_current_speaker_renames_later_labels(conversation):
    bus = EventBus()
    backend = NamingBackend("s2", {"name": "小李", "aliases": ["Xiao Li", "小丽", "小李"]})
    await Session(backend, bus).run(WavFileSource(conversation.path))
    later = [l for l in backend.labels if l.speaker_id == "s2"][1:]
    assert later and all(l.name == "小李" for l in later)
    assert all(l.name.startswith("说话人") for l in backend.labels if l.speaker_id != "s2")
    assert backend.tool_results == [("c1", "name_speaker", {"ok": True, "speaker": "说话人 2", "name": "小李", "note": "只在本次对话中有效"})]
    renamed = next(e for e in bus.history if e["type"] == "profile_renamed")
    assert renamed["aliases"] == ["Xiao Li", "小丽"]  # 去掉了和名字相同的别名
    assert [e["name"] for e in bus.history if e["type"] == "transcript" and e["speaker_id"] == "s2"][-1] == "小李"


async def test_naming_someone_else_by_their_label(conversation):
    backend = NamingBackend("s3", {"name": "小王", "speaker": "说话人1"})
    await Session(backend, EventBus()).run(WavFileSource(conversation.path))
    last_s1 = [l for l in backend.labels if l.speaker_id == "s1"][-1]
    assert last_s1.name == "小王"


async def test_naming_unknown_speaker_reports_an_error(conversation):
    backend = NamingBackend("s1", {"name": "小王", "speaker": "说话人 9"})
    await Session(backend, EventBus()).run(WavFileSource(conversation.path))
    assert backend.tool_results[0][2]["ok"] is False


async def test_names_are_forgotten_in_a_new_session(conversation):
    await Session(NamingBackend("s1", {"name": "小王"}), EventBus()).run(WavFileSource(conversation.path))
    fresh = FakeBackend()
    await Session(fresh, EventBus()).run(WavFileSource(conversation.path))
    assert fresh.labels[0].name == "说话人 1"


class ScriptedTranscriber:
    """按顺序返回预先写好的转写文字，用来模拟识别结果（例如把 Jennifer 听成杰夫）。"""

    def __init__(self, texts):
        self.texts = list(texts)

    def transcribe(self, samples):
        return self.texts.pop(0) if self.texts else ""


JENNIFER = {"name": "Jennifer", "aliases": ["珍妮弗", "珍妮", "Jenny"]}


async def run_named(conversation, texts, args=JENNIFER, when="s2"):
    bus, backend = EventBus(), NamingBackend(when, args)
    await Session(backend, bus, transcriber=ScriptedTranscriber(texts)).run(WavFileSource(conversation.path))
    return bus, backend


async def test_roster_with_aliases_is_sent_after_naming(conversation):
    _, backend = await run_named(conversation, [])
    rosters = [n for n in backend.notes if n.startswith("[房间里的人")]
    assert len(rosters) == 1
    assert "Jennifer（说话人 2，名字也可能被听成：珍妮弗、珍妮、Jenny）" in rosters[0]
    assert "说话人 1（还不知道名字）" in rosters[0]


async def test_roster_is_resent_only_when_it_changes(conversation):
    class RenameTwice(NamingBackend):
        async def send_speaker_label(self, label):
            await super().send_speaker_label(label)
            if label.speaker_id == "s3":  # s3 每次开口都"重复命名"一次，第三次改成另一个名字
                self.count = getattr(self, "count", 0) + 1
                name = "小王" if self.count < 3 else "王小明"
                self.emit(BackendEvent("tool_call", tool="name_speaker", args={"name": name}, call_id=f"r{self.count}"))

    backend = RenameTwice("s2", JENNIFER)
    await Session(backend, EventBus(), transcriber=ScriptedTranscriber([])).run(WavFileSource(conversation.path))
    rosters = [n for n in backend.notes if n.startswith("[房间里的人")]
    assert len(rosters) == len(set(rosters))  # 没有重复发送同样的名单
    assert any("小王（说话人 3）" in r for r in rosters)


async def test_misheard_name_produces_a_hint(conversation):
    # 6 句话依次的转写；第 4 句（说话人 2 被命名之后）出现了把 Jennifer 听成的"杰夫"
    texts = ["小李你周末打算干嘛", "我想去爬山", "好啊", "你知道这个杰夫是谁吗", "也算我一个", "周六见"]
    bus, backend = await run_named(conversation, texts)
    hints = [e for e in bus.history if e["type"] == "name_hint"]
    assert [(h["heard"], h["name"]) for h in hints] == [("杰夫", "Jennifer")]
    assert any(n.startswith("[提示：") and "“杰夫”可能是指 Jennifer" in n for n in backend.notes)


async def test_unrelated_names_do_not_produce_hints(conversation):
    texts = ["小李你周末打算干嘛", "我想去爬山", "好啊", "张三今天没来", "也算我一个", "周六见"]
    bus, _ = await run_named(conversation, texts)
    assert not [e for e in bus.history if e["type"] == "name_hint"]


async def test_the_same_mishearing_is_hinted_only_once(conversation):
    texts = ["你好", "我想去爬山", "杰夫呢", "杰夫说可以", "杰夫也去", "杰夫周六见"]
    bus, _ = await run_named(conversation, texts)
    assert len([e for e in bus.history if e["type"] == "name_hint"]) == 1
