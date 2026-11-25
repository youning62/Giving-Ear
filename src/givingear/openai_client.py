"""OpenAI 兼容的文字模型客户端（流式文字 + 函数调用）。DeepSeek 和国内多数模型都用这套接口。"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .tools import ToolSpec


class ChatError(Exception):
    """模型接口的错误，消息可以直接显示给用户。"""


def explain(status: int, body: str) -> str:
    hints = {
        401: "密钥不对或已失效，请到设置里重新填写",
        402: "账户余额不足，请到提供商的控制台充值",
        403: "没有权限访问这个模型",
        404: "接口地址或模型名不对，请检查设置",
        429: "请求太频繁或额度用完了，稍后再试",
    }
    detail = body.strip().replace("\n", " ")[:200]
    return f"{hints.get(status, '模型接口出错了')}（HTTP {status}）" + (f"：{detail}" if detail and status not in hints else "")


class OpenAIChatClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0, transport: httpx.AsyncBaseTransport | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    def _payload(self, messages: list[dict], tools: list[ToolSpec], stream: bool) -> dict:
        body: dict = {"model": self.model, "messages": messages, "stream": stream}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        return body

    async def chat(self, messages: list[dict], tools: list[ToolSpec]) -> AsyncIterator[tuple]:
        calls: dict[int, dict] = {}
        try:
            async with self._http.stream("POST", self.url, headers=self._headers, json=self._payload(messages, tools, True)) as r:
                if r.status_code != 200:
                    raise ChatError(explain(r.status_code, (await r.aread()).decode(errors="replace")))
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # 空行和 ": keep-alive" 之类的注释
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        choice = json.loads(data)["choices"][0]
                    except (ValueError, KeyError, IndexError):
                        continue
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield ("text", delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function") or {}
                        c["name"] = fn.get("name") or c["name"]
                        c["args"] += fn.get("arguments") or ""
        except httpx.HTTPError as exc:
            raise ChatError(f"连不上模型接口（{type(exc).__name__}）。请检查网络和接口地址") from exc
        for i in sorted(calls):
            c = calls[i]
            try:
                args = json.loads(c["args"] or "{}")
            except ValueError:
                args = {}
            yield ("tool_call", c["id"] or f"call_{i}", c["name"], args if isinstance(args, dict) else {})

    async def ping(self) -> str:
        """测试连接：发一个最小的请求，成功返回模型的回复，失败抛 ChatError。"""
        try:
            r = await self._http.post(self.url, headers=self._headers, json={**self._payload([{"role": "user", "content": "你好，请只回复“好”"}], [], False), "max_tokens": 8})
        except httpx.HTTPError as exc:
            raise ChatError(f"连不上模型接口（{type(exc).__name__}）。请检查网络和接口地址") from exc
        if r.status_code != 200:
            raise ChatError(explain(r.status_code, r.text))
        try:
            return r.json()["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError):
            raise ChatError("接口返回的格式不对，请确认它是 OpenAI 兼容接口") from None

    async def aclose(self) -> None:
        await self._http.aclose()
