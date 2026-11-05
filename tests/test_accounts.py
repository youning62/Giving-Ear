"""账号与加密声纹库：只断言对外可见的行为（登录结果、硬盘上的文件内容）。"""

import json

import numpy as np
import pytest

from givingear.accounts import AccountError, AccountStore, NoSuchAccount, WrongPassword

FAST = {"n": 2**4, "r": 8, "p": 1}  # 测试里把密码派生调得很便宜
PW = "correct horse"


@pytest.fixture
def store(tmp_path):
    return AccountStore(tmp_path, kdf_cost=FAST)


def emb(seed):
    return np.random.default_rng(seed).normal(size=192).astype(np.float32)


def test_login_with_the_right_password(store):
    store.create("Wang", PW, model="m1")
    vault = store.open("wang", PW)  # 用户名不区分大小写
    assert (vault.username, vault.model, vault.voiceprints) == ("wang", "m1", [])


def test_wrong_password_and_unknown_account_give_clear_errors(store):
    store.create("wang", PW)
    with pytest.raises(WrongPassword):
        store.open("wang", "not the password")
    with pytest.raises(NoSuchAccount):
        store.open("nobody", PW)


@pytest.mark.parametrize("bad", ["", "a/b", "../x", "a.b", "x" * 33, "a b"])
def test_usernames_cannot_escape_the_accounts_folder(store, bad):
    with pytest.raises(AccountError):
        store.create(bad, PW)


def test_chinese_usernames_are_allowed_and_names_are_unique(store):
    store.create("王家", PW)
    with pytest.raises(AccountError, match="已经被用"):
        store.create("王家", PW)
    store.create("wang", PW)
    with pytest.raises(AccountError, match="已经被用"):
        store.create("WANG", PW)
    assert store.list() == ["wang", "王家"]


def test_short_passwords_are_rejected(store):
    with pytest.raises(AccountError, match="至少"):
        store.create("wang", "short")


def test_voiceprints_survive_a_new_login_bit_for_bit(store):
    store.create("wang", PW)
    v = store.open("wang", PW)
    v.add("小王", [emb(1), emb(2)], aliases=["Xiao Wang"])
    v.save()
    again = store.open("wang", PW).voiceprints
    assert [x.name for x in again] == ["小王"] and again[0].aliases == ["Xiao Wang"]
    assert all(np.array_equal(a, b) for a, b in zip(again[0].embeddings, [emb(1), emb(2)]))


def test_nothing_readable_is_on_disk(store, tmp_path):
    store.create("wang", PW)
    v = store.open("wang", PW)
    v.add("小王", [emb(1)], aliases=["秘密别名"])
    v.save()
    d = tmp_path / "accounts" / "wang"
    blob = (d / "voiceprints.enc").read_bytes()
    assert "小王".encode() not in blob and "秘密别名".encode() not in blob and b"voiceprints" not in blob
    record = (d / "account.json").read_text()
    assert PW not in record and "小王" not in record
    assert not any(p.suffix in (".wav", ".caf", ".aiff") for p in tmp_path.rglob("*"))


def test_a_tampered_vault_is_refused(store, tmp_path):
    store.create("wang", PW)
    f = tmp_path / "accounts" / "wang" / "voiceprints.enc"
    data = bytearray(f.read_bytes())
    data[-1] ^= 1
    f.write_bytes(bytes(data))
    with pytest.raises(AccountError, match="损坏"):
        store.open("wang", PW)


def test_changing_the_password_keeps_the_voiceprints(store):
    store.create("wang", PW)
    v = store.open("wang", PW)
    v.add("小王", [emb(1)])
    v.save()
    store.change_password("wang", PW, "a brand new one")
    with pytest.raises(WrongPassword):
        store.open("wang", PW)
    assert [x.name for x in store.open("wang", "a brand new one").voiceprints] == ["小王"]


def test_wrong_old_password_cannot_change_it(store):
    store.create("wang", PW)
    with pytest.raises(WrongPassword):
        store.change_password("wang", "nope nope nope", "a brand new one")
    store.open("wang", PW)


def test_deleting_an_account_removes_everything(store, tmp_path):
    store.create("wang", PW)
    with pytest.raises(WrongPassword):
        store.delete("wang", "nope nope nope")
    store.delete("wang", PW)
    assert not (tmp_path / "accounts" / "wang").exists() and store.list() == []


def test_a_crash_while_saving_does_not_damage_the_vault(store, monkeypatch):
    store.create("wang", PW)
    v = store.open("wang", PW)
    v.add("小王", [emb(1)])
    v.save()
    v.add("小李", [emb(2)])
    monkeypatch.setattr("givingear.accounts.os.replace", lambda *a: (_ for _ in ()).throw(OSError("断电")))
    with pytest.raises(OSError):
        v.save()
    monkeypatch.undo()
    assert [x.name for x in store.open("wang", PW).voiceprints] == ["小王"]


def test_same_name_replaces_instead_of_duplicating(store):
    store.create("wang", PW)
    v = store.open("wang", PW)
    v.add("小王", [emb(1)])
    v.add(" 小王 ", [emb(2)])
    assert len(v.voiceprints) == 1 and np.array_equal(v.voiceprints[0].embeddings[0], emb(2))


def test_rename_and_remove(store):
    store.create("wang", PW)
    v = store.open("wang", PW)
    v.add("小王", [emb(1)])
    v.add("小李", [emb(2)])
    v.rename("小王", "王小明")
    with pytest.raises(AccountError, match="已经有"):
        v.rename("小李", "王小明")
    assert v.remove("小李") and not v.remove("小李")
    assert [x.name for x in v.voiceprints] == ["王小明"]


def test_the_vault_remembers_which_model_made_the_voiceprints(store):
    store.create("wang", PW, model="campplus-zh")
    assert store.open("wang", PW).model == "campplus-zh"
