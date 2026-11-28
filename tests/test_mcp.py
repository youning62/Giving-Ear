"""MCP 服务器：通过真实的 stdio 协议启动 `givingear mcp`，列出工具并调用。"""

import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED = {"start_listening", "stop_listening", "listening_status", "get_transcript", "list_speakers",
            "name_speaker", "register_speaker", "diarize_file"}


async def test_mcp_server_over_stdio(conversation, tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "givingear.cli", "mcp"],
                                   env={"GIVINGEAR_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        init = await client.initialize()
        assert init.server_info.name == "givingear"
        names = {t.name for t in (await client.list_tools()).tools}
        assert EXPECTED <= names
        status = await client.call_tool("listening_status", {})
        assert status.content[0].text == "没有在听。"
        result = await client.call_tool("diarize_file", {"path": str(conversation.path), "names": {"说话人 1": "小王"}})
        text = result.content[0].text
        assert "小王:" in text and "说话人 2:" in text and "说话人 3:" in text
