"""只当耳朵：对话记录、后台收音服务的 HTTP 接口、真实后台进程的启动和停止。"""

import asyncio
import time
import urllib.error
import urllib.request

import pytest

from givingear import listen
from givingear.audio import WavFileSource
from givingear.backend import NullBackend
from givingear.events import EventBus
from givingear.session import Session
from givingear.transcript import TranscriptLog, format_text


# ---------- 对话记录 ----------


def _t(uid, sid, text, wall, name=None):
    return {"type": "transcript", "utterance_id": uid, "speaker_id": sid, "name": name or f"说话人 {sid[1:]}",
            "text": text, "wall_t": wall, "start": 0.0, "audio_t": 2.0}


def test_renaming_updates_earlier_lines():
    log = TranscriptLog()
    now = time.time()
    log.handle({"type": "profile_created", "speaker_id": "s1", "name": "说话人 1"})
    log.handle(_t(1, "s1", "我叫 Jennifer", now))
    log.handle({"type": "profile_renamed", "speaker_id": "s1", "name": "Jennifer"})
    assert [r["speaker"] for r in log.query()] == ["Jennifer"]


def test_query_filters_by_time_speaker_and_count():
    log = TranscriptLog()
    now = time.time()
    log.handle(_t(1, "s1", "很久以前", now - 3600))
    log.handle(_t(2, "s2", "刚才", now - 30))
    log.handle(_t(3, "s1", "现在", now))
    assert [r["text"] for r in log.query(since_minutes=5)] == ["刚才", "现在"]
    assert [r["text"] for r in log.query(speaker="说话人1")] == ["很久以前", "现在"]  # 名字里的空格可写可不写
    assert [r["text"] for r in log.query(last=1)] == ["现在"]


def test_text_format_merges_consecutive_lines_of_one_speaker():
    log = TranscriptLog()
    now = time.time()
    for i, (sid, text) in enumerate([("s1", "明天"), ("s1", "几点开会？"), ("s2", "十点")]):
        log.handle(_t(i, sid, text, now + i))
    lines = format_text(log.query()).splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("说话人 1: 明天 几点开会？")
    assert lines[1].endswith("说话人 2: 十点")


# ---------- 后台收音服务（同一进程里，用 WAV 代替麦克风） ----------


async def _wait(pred, timeout=60):
    deadline = time.time() + timeout
    while not pred():
        assert time.time() < deadline, "等待超时"
        await asyncio.sleep(0.1)


@pytest.fixture
async def service(conversation):
    session = Session(NullBackend(), EventBus())
    svc = listen.ListenService(session, WavFileSource(conversation.path))
    ready = {}
    task = asyncio.create_task(svc.run(0, lambda port, token: ready.update(port=port, token=token)))
    await _wait(lambda: svc.state != "listening")  # WAV 放完
    await asyncio.sleep(0.5)  # 让最后一句的转写完成
    yield svc, {"port": ready["port"], "token": ready["token"]}
    svc.request_stop()
    await asyncio.wait_for(task, 10)


def call(state, method, path, body=None):
    return listen.request(method, path, body, state=state)


async def test_service_reports_who_said_what(service, conversation):
    svc, state = service
    lines = await asyncio.to_thread(call, state, "GET", "/transcript")
    assert len(lines["lines"]) == len(conversation.turns)
    speakers = await asyncio.to_thread(call, state, "GET", "/speakers")
    assert len(speakers["speakers"]) == 3
    status = await asyncio.to_thread(call, state, "GET", "/status")
    assert status["state"] == "ended" and status["utterances"] == len(conversation.turns)


async def test_naming_through_the_service_renames_history(service):
    svc, state = service
    result = await asyncio.to_thread(call, state, "POST", "/name", {"speaker": "说话人 2", "name": "Jennifer"})
    assert result["ok"]
    await asyncio.sleep(0.2)
    rows = (await asyncio.to_thread(call, state, "GET", "/transcript?speaker=Jennifer"))["lines"]
    assert rows and all(r["speaker"] == "Jennifer" for r in rows)


async def test_requests_without_the_token_are_refused(service):
    _, state = service

    def bad():
        req = urllib.request.Request(f"http://127.0.0.1:{state['port']}/transcript")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        return err.value.code

    assert await asyncio.to_thread(bad) == 401
    with pytest.raises(listen.ListenError):
        await asyncio.to_thread(call, {**state, "token": "wrong"}, "GET", "/status")


async def test_registering_without_an_account_explains_why(service):
    _, state = service
    result = await asyncio.to_thread(call, state, "POST", "/register", {"speaker": "说话人 1", "name": "小王"})
    assert result["ok"] is False and "账号" in result["error"]


# ---------- 真实的后台进程 ----------


def test_background_process_starts_answers_and_stops(conversation, tmp_path, monkeypatch):
    monkeypatch.setenv("GIVINGEAR_HOME", str(tmp_path))
    started = listen.start(wav=str(conversation.path), timeout=120)
    try:
        assert started["already_running"] is False
        assert (tmp_path / "listen.json").stat().st_mode & 0o077 == 0  # 令牌只有本人能读
        assert listen.start(wav=str(conversation.path))["already_running"] is True  # 不会重复启动
        deadline = time.time() + 90
        while len(listen.transcript()) < len(conversation.turns):
            assert time.time() < deadline, "等待转写超时：" + listen._log_tail()
            time.sleep(0.5)
        assert len(listen.speakers()) == 3
    finally:
        stopped = listen.stop()
    assert stopped["was_running"]
    assert listen.status() == {"state": "not_running"}
    assert not (tmp_path / "listen.json").exists()
    with pytest.raises(listen.ListenError):
        listen.transcript()
