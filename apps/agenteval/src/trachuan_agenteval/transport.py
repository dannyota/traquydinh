import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from .oauth import Token


class Bearer(httpx2.Auth):
    def __init__(self, token: Token) -> None:
        self.token = token

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = "Bearer " + await self.token.access()
        yield request


@dataclass
class ToolResult:
    text: str
    structured: dict


class MCP:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def tools(self) -> list[dict]:
        tools, cursor, seen = [], None, set()
        while True:
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await self.session.list_tools(params=params)
            tools.extend(
                t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in page.tools
            )
            cursor = page.next_cursor
            if not cursor:
                break
            if cursor in seen:
                raise RuntimeError("MCP tool paging did not progress")
            seen.add(cursor)
        names = [t["name"] for t in tools]
        if not names or len(names) != len(set(names)):
            raise RuntimeError("invalid MCP tool list")
        return tools

    async def call(self, name: str, arguments: dict) -> ToolResult:
        result = await self.session.call_tool(name, arguments)
        raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        blocks = raw.get("content", [])
        if any(b.get("type") != "text" for b in blocks):
            raise RuntimeError("non-text MCP result is unsupported")
        text = "\n".join(b["text"] for b in blocks)
        if raw.get("isError"):
            text = json.dumps({"isError": True, "content": text}, ensure_ascii=False)
        structured = raw.get("structuredContent") or {}
        if not isinstance(structured, dict):
            raise RuntimeError("invalid structured MCP result")
        return ToolResult(text, structured)


@asynccontextmanager
async def connect(
    server: str, token: Token, transport: httpx2.AsyncBaseTransport | None = None
) -> AsyncIterator[MCP]:
    async with httpx2.AsyncClient(
        auth=Bearer(token), trust_env=False, timeout=60, transport=transport
    ) as http:
        async with streamable_http_client(server + "/mcp", http_client=http) as streams:
            async with ClientSession(*streams, read_timeout_seconds=60) as session:
                await session.initialize()
                yield MCP(session)
