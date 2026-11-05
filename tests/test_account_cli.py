"""命令行：账号与声纹管理。用环境变量隔离数据目录、提供密码。"""

import numpy as np
import pytest

from givingear import cli, models
from givingear.accounts import AccountStore

PW = "correct horse"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIVINGEAR_HOME", str(tmp_path))
    monkeypatch.setenv("GIVINGEAR_PASSWORD", PW)
    return tmp_path


def run(*argv):
    cli.main(list(argv))


def test_create_list_change_password_and_delete(capsys, monkeypatch):
    run("account", "create", "wang")
    run("account", "list")
    assert "wang" in capsys.readouterr().out
    monkeypatch.setenv("GIVINGEAR_NEW_PASSWORD", "a brand new one")
    run("account", "passwd", "wang")
    monkeypatch.setenv("GIVINGEAR_PASSWORD", "a brand new one")
    run("account", "delete", "wang")
    run("account", "list")
    assert "还没有任何账号" in capsys.readouterr().out


def test_wrong_password_exits_with_a_chinese_message(monkeypatch):
    run("account", "create", "wang")
    monkeypatch.setenv("GIVINGEAR_PASSWORD", "not the password")
    with pytest.raises(SystemExit, match="密码不对"):
        run("speakers", "list")


def test_listing_when_nobody_is_registered(capsys):
    run("account", "create", "wang")
    run("speakers", "list")
    assert "暂无已登记的说话人" in capsys.readouterr().out


def register(names, home):
    vault = AccountStore(home).open("wang", PW)
    for i, n in enumerate(names):
        vault.add(n, [np.random.default_rng(i).normal(size=192).astype(np.float32)])
    vault.save()


def test_list_rename_and_delete_speakers(capsys, home):
    run("account", "create", "wang")
    register(["小王", "小李"], home)
    run("speakers", "list")
    out = capsys.readouterr().out
    assert "小王" in out and "小李" in out and "登记于" in out
    run("speakers", "rename", "小李", "李明")
    run("speakers", "delete", "小王")
    assert [v.name for v in AccountStore(home).open("wang", PW).voiceprints] == ["李明"]


def test_deleting_a_missing_name_is_not_an_error(capsys):
    run("account", "create", "wang")
    run("speakers", "delete", "没这个人")
    assert "没有叫“没这个人”的档案" in capsys.readouterr().out


def test_several_accounts_need_an_explicit_choice():
    run("account", "create", "wang")
    run("account", "create", "li")
    with pytest.raises(SystemExit, match="--account"):
        run("speakers", "list")
    run("speakers", "--account", "li", "list")


def test_creating_an_existing_account_or_a_bad_name_fails_clearly():
    run("account", "create", "wang")
    with pytest.raises(SystemExit, match="已经被用"):
        run("account", "create", "wang")
    with pytest.raises(SystemExit, match="用户名只能"):
        run("account", "create", "../x")


def test_a_deleted_person_is_a_stranger_next_time(conversation, home):
    import asyncio

    from givingear.audio import WavFileSource
    from givingear.backend import FakeBackend
    from givingear.events import EventBus
    from givingear.session import Session
    from givingear.speakers import SessionProfiles

    async def go(vault):
        profiles, backend = SessionProfiles(), FakeBackend()
        await Session(backend, EventBus(), profiles=profiles, vault=vault).run(WavFileSource(conversation.path))
        return profiles, backend

    run("account", "create", "wang")
    profiles, _ = asyncio.run(go(None))
    vault = AccountStore(home).open("wang", PW)
    vault.add("小王", profiles.profiles[0].embeddings)
    vault.save()
    _, backend = asyncio.run(go(AccountStore(home).open("wang", PW)))
    assert backend.labels[0].name == "小王"
    run("speakers", "delete", "小王")
    _, backend = asyncio.run(go(AccountStore(home).open("wang", PW)))
    assert backend.labels[0].name == "说话人 1"


def test_run_with_a_wrong_password_never_starts_listening(monkeypatch, capsys):
    run("account", "create", "wang")
    monkeypatch.setenv("GIVINGEAR_PASSWORD", "not the password")
    with pytest.raises(SystemExit, match="密码不对"):
        run("--account", "wang", "--fake", "--wav", "/nonexistent.wav", "--port", "0")
    assert "正在听" not in capsys.readouterr().out
