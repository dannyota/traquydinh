import asyncio
from types import SimpleNamespace

from trachuan_agenteval.transport import MCP


class FakeSession:
    def __init__(self):
        self.pages = []

    async def list_tools(self, **kwargs):
        self.pages.append(kwargs)
        n = len(self.pages)
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    model_dump=lambda **kwargs: {
                        "name": "tool" + str(n),
                        "inputSchema": {"type": "object"},
                    }
                )
            ],
            next_cursor="next" if n == 1 else None,
        )

    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            model_dump=lambda **kwargs: {
                "content": [{"type": "text", "text": '{"text":"đúng"}'}],
                "structuredContent": {"text": "đúng"},
                "isError": False,
            }
        )


def test_tool_discovery_follows_paging_and_sends_text_once():
    async def run():
        session = FakeSession()
        mcp = MCP(session)
        tools = await mcp.tools()
        assert [t["name"] for t in tools] == ["tool1", "tool2"]
        assert len(session.pages) == 2
        result = await mcp.call("tool1", {})
        assert result.text == '{"text":"đúng"}'
        assert result.structured == {"text": "đúng"}

    asyncio.run(run())


def test_mcp_sdk_initialize_discovery_and_call_with_recorded_http():
    import json

    import httpx2

    from trachuan_agenteval.transport import connect

    class Token:
        async def access(self):
            return "synthetic-access"

    def handle(request):
        assert request.headers["authorization"] == "Bearer synthetic-access"
        if request.method != "POST":
            return httpx2.Response(405)
        data = json.loads(request.content)
        method = data["method"]
        if method == "notifications/initialized":
            return httpx2.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "fixture_tool", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {
                "content": [{"type": "text", "text": '{"ok":true}'}],
                "structuredContent": {"ok": True},
            }
        else:
            raise AssertionError(method)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={"jsonrpc": "2.0", "id": data["id"], "result": result},
        )

    async def run():
        async with connect(
            "http://127.0.0.1:29080", Token(), transport=httpx2.MockTransport(handle)
        ) as mcp:
            assert (await mcp.tools())[0]["inputSchema"] == {"type": "object"}
            assert (await mcp.call("fixture_tool", {})).structured == {"ok": True}

    asyncio.run(run())
