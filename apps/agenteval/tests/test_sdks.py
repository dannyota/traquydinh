import asyncio
import json

import httpx2
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from trachuan_agenteval.providers import AnthropicProvider, OpenAIProvider

TOOLS = [{"name": "guide", "inputSchema": {"type": "object", "properties": {}}}]


def test_official_anthropic_sdk_with_recorded_response_and_fake_transport():
    def handle(request):
        body = json.loads(request.content)
        assert body["thinking"]["type"] == "adaptive"
        assert body["tools"][-1]["cache_control"]["type"] == "ephemeral"
        return httpx2.Response(
            200,
            json={
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "content": [{"type": "text", "text": "fixture answer"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 30,
                    "cache_read_input_tokens": 40,
                },
            },
        )

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
            async with AsyncAnthropic(
                api_key="synthetic-key", http_client=http, max_retries=0
            ) as c:
                result = await AnthropicProvider(c, "claude-sonnet-5-5", "high").request(
                    "fixture system", [{"role": "user", "content": "fixture"}], TOOLS
                )
                assert result.usage.summed_input == 170
                assert result.stop == "final"

    asyncio.run(run())


def test_official_openai_sdk_with_recorded_response_and_fake_transport():
    def handle(request):
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["tools"][0]["strict"] is False
        return httpx2.Response(
            200,
            json={
                "id": "resp_fixture",
                "object": "response",
                "created_at": 0,
                "model": "openai-fixture",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_fixture",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "fixture"}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 5},
                    "output_tokens_details": {"reasoning_tokens": 10},
                },
            },
        )

    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
            async with AsyncOpenAI(api_key="synthetic-key", http_client=http, max_retries=0) as c:
                result = await OpenAIProvider(c, "openai-fixture").request(
                    "fixture system", [{"role": "user", "content": "fixture"}], TOOLS
                )
                assert result.usage.uncached_input == 55
                assert result.usage.cache_write == 5
                assert result.stop == "refusal"

    asyncio.run(run())
