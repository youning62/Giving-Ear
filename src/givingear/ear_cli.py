"""只当耳朵的命令：给 AI 助手（Claude Code 等）和人用。

    givingear listen start|stop|status   在后台开始 / 停止收音，或者查看状态
    givingear transcript                 谁说了什么（默认文字，--json 输出结构化数据）
    givingear who                        房间里有哪些人
    givingear name 说话人2 Jennifer       给某个人命名（之前的记录也会改成新名字）
    givingear register 说话人2 [名字]     把声纹永久登记到账号（需要 listen start --account）
    givingear diarize 录音.m4a            给录音文件按说话人转写

输出尽量短，方便 AI 直接读；出错时退出码非 0，错误写到标准错误。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import listen
from .transcript import format_text

EAR_COMMANDS = ("listen", "transcript", "who", "name", "register", "diarize")


def add_parsers(sub: argparse._SubParsersAction) -> None:
    lis = sub.add_parser("listen", help="在后台收音并分辨说话人（不接 AI 模型）")
    ls = lis.add_subparsers(dest="action", required=True)
    st = ls.add_parser("start", help="开始在后台收音")
    st.add_argument("--account", help="解锁这个账号里已登记的声纹，并允许登记新的（需要输入密码）")
    st.add_argument("--max-minutes", type=float, default=listen.DEFAULT_MAX_MINUTES, help="最长收音时间，到时自动停止")
    st.add_argument("--wav", help=argparse.SUPPRESS)  # 测试用：用 WAV 文件代替麦克风
    sp = ls.add_parser("stop", help="停止收音，内存里的对话记录随之丢弃")
    sp.add_argument("--save", metavar="文件", help="停止前把对话记录保存成文字文件")
    ls.add_parser("status", help="是否在听、听了多久、有几个人")

    tr = sub.add_parser("transcript", help="输出谁说了什么")
    tr.add_argument("--since", type=float, metavar="分钟", help="只看最近几分钟")
    tr.add_argument("--speaker", help="只看某个人")
    tr.add_argument("--last", type=int, metavar="N", help="只看最后 N 句")
    tr.add_argument("--json", action="store_true")

    who = sub.add_parser("who", help="房间里有哪些人")
    who.add_argument("--json", action="store_true")

    nm = sub.add_parser("name", help="给某个人命名，只在这次收音中有效")
    nm.add_argument("speaker", help="现在的名字或编号，例如“说话人 2”")
    nm.add_argument("new_name")
    nm.add_argument("--alias", action="append", default=[], help="名字的其他写法、音译，可写多次")

    rg = sub.add_parser("register", help="把某个人的声纹永久登记到账号")
    rg.add_argument("speaker")
    rg.add_argument("new_name", nargs="?", default="")

    di = sub.add_parser("diarize", help="给录音文件按说话人转写（本机处理，不联网）")
    di.add_argument("file")
    di.add_argument("--name", action="append", default=[], metavar="说话人1=小王", help="预先给说话人起名，可写多次")
    di.add_argument("--json", action="store_true")


def _print(data, as_json: bool, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1) if as_json else text)


def _speakers_text(rows: list[dict]) -> str:
    if not rows:
        return "还没有人说话"
    return "\n".join(
        f"{r['name']}（{r['speaker_id']}）：{r['utterances']} 句，{r['seconds']} 秒" + ("，已登记" if r["registered"] else "")
        for r in rows
    )


def run(args: argparse.Namespace) -> None:
    try:
        _run(args)
    except listen.ListenError as exc:
        sys.exit(f"！{exc}")


def _run(args: argparse.Namespace) -> None:
    match args.cmd:
        case "listen" if args.action == "start":
            password = None
            if args.account:
                from .account_cli import read_password

                password = read_password(f"账号 {args.account} 的密码：")
            print("正在启动……（第一次要下载约 400 MB 的本地模型）", file=sys.stderr)
            s = listen.start(args.account, args.max_minutes, args.wav, password)
            if s["already_running"]:
                print(f"已经在听了：{s['minutes']} 分钟，{s['speakers']} 个人，{s['utterances']} 句")
            else:
                print(f"开始在后台收音，最长 {s['max_minutes']:g} 分钟。记得告诉房间里的人正在记录。"
                      + (f"已解锁账号 {s['account']} 的声纹。" if s["account"] else ""))
        case "listen" if args.action == "stop":
            if args.save and listen.read_state():
                rows = listen.transcript()
                with open(args.save, "w", encoding="utf-8") as f:
                    f.write(format_text(rows) + "\n")
            r = listen.stop()
            if not r["was_running"]:
                print("本来就没有在听")
            else:
                print(f"已停止。共 {r['minutes']} 分钟、{r['utterances']} 句"
                      + (f"，对话记录已保存到 {args.save}" if args.save else "，对话记录已丢弃"))
        case "listen":
            s = listen.status()
            if s["state"] == "not_running":
                print("没有在听")
            else:
                state = {"listening": "正在听", "ended": "音频已结束", "error": "出错了"}.get(s["state"], s["state"])
                print(f"{state}：{s['minutes']} 分钟（最长 {s['max_minutes']:g}），{s['speakers']} 个人，{s['utterances']} 句"
                      + (f"，账号 {s['account']}" if s["account"] else "") + (f"\n！{s['error']}" if s["error"] else ""))
        case "transcript":
            rows = listen.transcript(args.since, args.speaker, args.last)
            _print(rows, args.json, format_text(rows) or "（还没有对话记录）")
        case "who":
            rows = listen.speakers()
            _print(rows, args.json, _speakers_text(rows))
        case "name":
            r = listen.name(args.speaker, args.new_name, args.alias)
            _result(r, f"{args.speaker} 现在叫 {args.new_name}（只在这次收音中有效）")
        case "register":
            r = listen.register(args.speaker, args.new_name)
            if r.get("need_more_speech"):
                sys.exit(f"！这个人说的话还不够（{r['have_s']} / {r['need_s']} 秒），请他再随便说几句")
            _result(r, r.get("note", "已登记"))
        case "diarize":
            from .diarize import diarize

            names = dict(n.split("=", 1) for n in args.name if "=" in n)
            try:
                result = asyncio.run(diarize(args.file, names))
            except (FileNotFoundError, RuntimeError) as exc:
                sys.exit(f"！{exc}")
            _print(result, args.json, format_text(result["lines"]) or "（没有检测到说话）")


def _result(r: dict, ok_text: str) -> None:
    if not r.get("ok"):
        sys.exit(f"！{r.get('error', '失败')}")
    print(ok_text)
