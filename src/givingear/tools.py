"""AI 在对话中可以调用的工具。定义与具体模型无关，各后端负责转成自己的格式。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


NAME_SPEAKER = ToolSpec(
    name="name_speaker",
    description=(
        "记住房间里某个人的名字，只在本次对话中有效。"
        "当有人自我介绍（例如“我叫小王”），或者别人告诉你某人叫什么（例如“说话人 2 是小王”）时调用。"
        "调用后，这个人之后的说话人标签会显示新名字。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "这个人的名字，按本人习惯的写法，例如 Jennifer 或 小王"},
            "speaker": {
                "type": "string",
                "description": "要命名的人当前在标签中的名字，例如“说话人 2”。省略时表示刚才说话的人",
            },
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "这个名字常见的其他写法、中文音译、昵称，以及语音识别容易听成的样子，例如 Jennifer → 珍妮弗、珍妮、Jenny",
            },
        },
        "required": ["name"],
    },
)

REGISTER_SPEAKER = ToolSpec(
    name="register_speaker",
    description=(
        "把某个人的声纹永久保存到当前登录的账号里，以后每次打开都能认出他。"
        "只有当这个人明确表示要登记时才调用，例如“帮我注册”“记住我的声音”“以后都认得我”。"
        "只是自我介绍、聊天里提到名字，都不算登记意图，不要调用。"
        "如果返回需要更多语音，请对方再随便说几句，说够了再调用一次。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "这个人的名字；这个人已经有名字时可以省略"},
            "speaker": {"type": "string", "description": "要登记的人当前在标签中的名字，例如“说话人 2”。省略时表示刚才说话的人"},
            "aliases": {"type": "array", "items": {"type": "string"}, "description": "名字的其他写法、音译、容易被听成的样子"},
        },
    },
)

ALL_TOOLS = [NAME_SPEAKER]  # 没有登录账号时只有命名工具
ACCOUNT_TOOLS = [NAME_SPEAKER, REGISTER_SPEAKER]  # 登录账号后，才能登记声纹
