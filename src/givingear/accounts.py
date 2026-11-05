"""本地账号与加密声纹库。账号是一个"空间"，名下存多人的声纹；声纹库用密码派生的密钥加密。

硬盘上只有：账号记录（用户名、密码派生参数、密码校验值）和加密后的声纹库。没有密码，没有音频。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = 1
USERNAME_RE = re.compile(r"^[\w-]{1,32}$")  # 字母、数字、下划线、连字符（含中文），不可能含路径分隔符
MIN_PASSWORD_LEN = 8
KDF_COST = {"n": 2**15, "r": 8, "p": 1}


class AccountError(Exception):
    """账号相关的错误，消息可以直接显示给用户。"""


class NoSuchAccount(AccountError):
    pass


class WrongPassword(AccountError):
    pass


def default_home() -> Path:
    return Path(os.environ.get("GIVINGEAR_HOME", Path.home() / ".givingear"))


def normalize_username(username: str) -> str:
    name = username.strip().casefold()
    if not USERNAME_RE.fullmatch(name):
        raise AccountError("用户名只能包含字母、数字、下划线和连字符，长度 1–32")
    return name


def _derive(password: str, salt: bytes, cost: dict) -> tuple[bytes, bytes]:
    """一次 scrypt 派生出 64 字节：前 32 字节是加密密钥，后 32 字节只用来校验密码。"""
    k = hashlib.scrypt(password.encode(), salt=salt, n=cost["n"], r=cost["r"], p=cost["p"], dklen=64, maxmem=2**28)
    return k[:32], hashlib.sha256(k[32:]).digest()


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # 原子替换：中途崩溃只会留下 .tmp，不会损坏原文件


@dataclass
class Voiceprint:
    name: str
    embeddings: list[np.ndarray]
    aliases: list[str] = field(default_factory=list)
    registered_at: float = field(default_factory=time.time)


def _decrypt(path: Path, key: bytes, username: str) -> tuple[str, list[Voiceprint]]:
    blob = path.read_bytes()
    try:
        body = json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], username.encode()))
    except InvalidTag:
        raise AccountError("声纹库已损坏或被改动，无法解密") from None
    vps = [Voiceprint(v["name"], [np.array(e, np.float32) for e in v["embeddings"]], v["aliases"], v["registered_at"])
           for v in body["voiceprints"]]
    return body["model"], vps


class Vault:
    """已解锁的声纹库。改动要调用 save() 才会写入硬盘。"""

    def __init__(self, path: Path, key: bytes, username: str, model: str, voiceprints: list[Voiceprint]):
        self._path, self._key = path, key
        self.username, self.model = username, model
        self.voiceprints = voiceprints

    def reload(self) -> None:
        """重新读一遍硬盘上的声纹库（别的地方——例如网页——可能刚改过），避免互相覆盖。"""
        self.voiceprints = _decrypt(self._path, self._key, self.username)[1]

    def find(self, name: str) -> Voiceprint | None:
        key = "".join(name.split()).casefold()
        return next((v for v in self.voiceprints if "".join(v.name.split()).casefold() == key), None)

    def add(self, name: str, embeddings: list[np.ndarray], aliases: list[str] | None = None) -> Voiceprint:
        """登记一个人；同名档案视为更新，不会产生重复。"""
        name = name.strip()
        if not name:
            raise AccountError("名字不能为空")
        if not embeddings:
            raise AccountError("没有声纹特征可以保存")
        old = self.find(name)
        if old:
            self.voiceprints.remove(old)
        v = Voiceprint(name, [np.asarray(e, np.float32) for e in embeddings], list(aliases or []))
        self.voiceprints.append(v)
        return v

    def rename(self, old: str, new: str) -> None:
        v = self.find(old)
        if v is None:
            raise AccountError(f"没有叫“{old}”的档案")
        new = new.strip()
        if not new:
            raise AccountError("名字不能为空")
        if (other := self.find(new)) and other is not v:
            raise AccountError(f"已经有叫“{new}”的档案")
        v.name = new

    def remove(self, name: str) -> bool:
        v = self.find(name)
        if v:
            self.voiceprints.remove(v)
        return v is not None

    def save(self) -> None:
        body = {
            "model": self.model,
            "voiceprints": [
                {"name": v.name, "aliases": v.aliases, "registered_at": v.registered_at,
                 "embeddings": [e.tolist() for e in v.embeddings]}
                for v in self.voiceprints
            ],
        }
        nonce = secrets.token_bytes(12)
        blob = nonce + AESGCM(self._key).encrypt(nonce, json.dumps(body).encode(), self.username.encode())
        _atomic_write(self._path, blob)


class AccountStore:
    def __init__(self, home: Path | None = None, kdf_cost: dict | None = None):
        self.home = Path(home) if home else default_home()
        self.kdf_cost = kdf_cost or KDF_COST

    def _dir(self, username: str) -> Path:
        return self.home / "accounts" / normalize_username(username)

    def list(self) -> list[str]:
        root = self.home / "accounts"
        return sorted(p.name for p in root.iterdir() if (p / "account.json").exists()) if root.exists() else []

    def create(self, username: str, password: str, model: str = "") -> str:
        d = self._dir(username)
        if (d / "account.json").exists():
            raise AccountError("这个用户名已经被用了")
        if len(password) < MIN_PASSWORD_LEN:
            raise AccountError(f"密码至少 {MIN_PASSWORD_LEN} 位")
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        salt = secrets.token_bytes(16)
        key, verifier = _derive(password, salt, self.kdf_cost)
        record = {"version": FORMAT_VERSION, "username": d.name, "created_at": time.time(),
                  "kdf": {"salt": salt.hex(), **self.kdf_cost}, "verifier": verifier.hex()}
        Vault(d / "voiceprints.enc", key, d.name, model, []).save()
        _atomic_write(d / "account.json", json.dumps(record, ensure_ascii=False).encode())
        return d.name

    def _unlock(self, username: str, password: str) -> tuple[Path, dict, bytes]:
        d = self._dir(username)
        try:
            record = json.loads((d / "account.json").read_text())
        except FileNotFoundError:
            raise NoSuchAccount("没有这个账号") from None
        kdf = record["kdf"]
        key, verifier = _derive(password, bytes.fromhex(kdf["salt"]), kdf)
        if not hmac.compare_digest(verifier.hex(), record["verifier"]):
            raise WrongPassword("密码不对")
        return d, record, key

    def open(self, username: str, password: str) -> Vault:
        d, record, key = self._unlock(username, password)
        model, vps = _decrypt(d / "voiceprints.enc", key, record["username"])
        return Vault(d / "voiceprints.enc", key, record["username"], model, vps)

    def change_password(self, username: str, old: str, new: str) -> None:
        if len(new) < MIN_PASSWORD_LEN:
            raise AccountError(f"密码至少 {MIN_PASSWORD_LEN} 位")
        vault = self.open(username, old)
        d = self._dir(username)
        record = json.loads((d / "account.json").read_text())
        salt = secrets.token_bytes(16)
        key, verifier = _derive(new, salt, self.kdf_cost)
        vault._key = key
        vault.save()  # 先用新密钥写好声纹库，再更新账号记录
        record["kdf"], record["verifier"] = {"salt": salt.hex(), **self.kdf_cost}, verifier.hex()
        _atomic_write(d / "account.json", json.dumps(record, ensure_ascii=False).encode())

    def delete(self, username: str, password: str) -> None:
        d, _, _ = self._unlock(username, password)
        shutil.rmtree(d)
