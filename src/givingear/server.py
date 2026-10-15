"""本地 WebSocket 事件流：任何客户端连上来，就能实时收到核心发出的事件（JSON，一条消息一个事件）。"""

from __future__ import annotations

import json

from websockets.asyncio.server import Server, ServerConnection, serve

from .events import EventBus

DEFAULT_HOST = "127.0.0.1"  # 只监听本机，不对局域网开放
DEFAULT_PORT = 8765


async def start_server(bus: EventBus, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> Server:
    async def handler(ws: ServerConnection) -> None:
        q = bus.subscribe()
        try:
            while True:
                e = await q.get()
                await ws.send(json.dumps(e, ensure_ascii=False))
                if e["type"] == "closed":
                    break
        finally:
            bus.unsubscribe(q)

    return await serve(handler, host, port)
