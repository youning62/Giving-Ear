"""命令行里的账号与声纹管理：givingear account ...、givingear speakers ...。"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time

from . import models
from .accounts import AccountError, AccountStore, Vault


def read_password(prompt: str = "密码：") -> str:
    """优先用环境变量 GIVINGEAR_PASSWORD（供脚本用），否则用不回显的提示输入。"""
    return os.environ.get("GIVINGEAR_PASSWORD") or getpass.getpass(prompt)


def pick_account(store: AccountStore, name: str | None) -> str:
    if name:
        return name
    names = store.list()
    if len(names) == 1:
        return names[0]
    raise AccountError("请用 --account 指定账号" + (f"（现有：{'、'.join(names)}）" if names else "，还没有任何账号，先运行 givingear account create"))


def open_vault(store: AccountStore, name: str | None) -> Vault:
    return store.open(pick_account(store, name), read_password())


def add_parsers(sub: argparse._SubParsersAction) -> None:
    acc = sub.add_parser("account", help="管理账号")
    a = acc.add_subparsers(dest="action", required=True)
    for action, text in [("create", "创建账号"), ("list", "列出账号"), ("passwd", "修改密码"), ("delete", "删除账号和它的全部声纹")]:
        p = a.add_parser(action, help=text)
        if action != "list":
            p.add_argument("username")
    spk = sub.add_parser("speakers", help="管理账号里已登记的声纹")
    spk.add_argument("--account", help="账号名；只有一个账号时可以省略")
    s = spk.add_subparsers(dest="action", required=True)
    s.add_parser("list", help="列出已登记的人")
    s.add_parser("delete", help="删除一个人的声纹（让程序彻底忘记他）").add_argument("name")
    r = s.add_parser("rename", help="给已登记的人改名")
    r.add_argument("old")
    r.add_argument("new")


def run(args: argparse.Namespace, store: AccountStore | None = None, out=None) -> None:
    store = store or AccountStore()
    out = out or sys.stdout
    say = lambda s: print(s, file=out)
    try:
        if args.cmd == "account":
            _account(args, store, say)
        else:
            _speakers(args, store, say)
    except AccountError as exc:
        sys.exit(f"！{exc}")


def _account(args, store: AccountStore, say) -> None:
    match args.action:
        case "list":
            names = store.list()
            say("\n".join(names) if names else "还没有任何账号")
        case "create":
            password = os.environ.get("GIVINGEAR_PASSWORD") or _new_password()
            name = store.create(args.username, password, model=models.SPEAKER_MODEL[0])
            say(f"已创建账号 {name}。声纹库已加密保存；忘记密码就无法找回里面的声纹。")
        case "passwd":
            old = read_password("原密码：")
            new = os.environ.get("GIVINGEAR_NEW_PASSWORD") or _new_password("新密码：")
            store.change_password(args.username, old, new)
            say("密码已修改")
        case "delete":
            store.delete(args.username, read_password("输入密码确认删除："))
            say(f"已删除账号 {args.username} 和它的全部声纹")


def _new_password(prompt: str = "设置密码：") -> str:
    first = getpass.getpass(prompt)
    if first != getpass.getpass("再输入一次："):
        raise AccountError("两次输入的密码不一样")
    return first


def _speakers(args, store: AccountStore, say) -> None:
    vault = open_vault(store, args.account)
    match args.action:
        case "list":
            if not vault.voiceprints:
                say("暂无已登记的说话人")
            for v in vault.voiceprints:
                say(f"{v.name}\t登记于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(v.registered_at))}")
        case "delete":
            if not vault.remove(args.name):
                say(f"没有叫“{args.name}”的档案")
                return
            vault.save()
            say(f"已删除 {args.name} 的声纹")
        case "rename":
            vault.rename(args.old, args.new)
            vault.save()
            say(f"已改名：{args.old} → {args.new}")
