import asyncio
from types import SimpleNamespace

import pytest

from trachuan_agenteval.agent import run_task
from trachuan_agenteval.providers import AnthropicProvider, OpenAIProvider, translate_tools
from trachuan_agenteval.scoring import parse_citations
from trachuan_agenteval.usage import Usage

TOOLS = [
    {
        "name": "search",
        "description": "Search",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
]
HANDLE = "vn:vbpl:synthetic#dieu-3/khoan-1"


class Response(dict):
    def model_dump(self, **kwargs):
        return dict(self)


class FakeClient:
    def __init__(self, responses):
        self.pending = iter(responses)
        self.requests = []
        self.messages = self.responses = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return Response(next(self.pending))


class FakeMCP:
    def __init__(self):
        self.calls = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(text='{"complete":true,"text":"đúng"}', structured={})


def test_schema_translation_preserves_mcp_schema_and_caches_anthropic_tools():
    anthropic = translate_tools(TOOLS, "anthropic")
    openai = translate_tools(TOOLS, "openai")
    assert anthropic[0]["input_schema"] == TOOLS[0]["inputSchema"]
    assert anthropic[-1]["cache_control"] == {"type": "ephemeral"}
    assert openai[0]["parameters"] == TOOLS[0]["inputSchema"]
    assert openai[0]["type"] == "function"
    assert openai[0]["strict"] is False
    assert "cache_control" not in TOOLS[0]


def test_citation_parser_only_accepts_fixed_form_and_deduplicates():
    answer = f"[[cite:{HANDLE}]] [[cite:{HANDLE}]] {HANDLE} [[cite:invalid]]"
    assert parse_citations(answer) == [HANDLE]


def anthropic_reply(content, reason="end_turn"):
    return {
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": reason,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 3,
        },
    }


def test_anthropic_loop_preserves_thinking_and_forces_mode_detail():
    thinking = {"type": "thinking", "thinking": "synthetic", "signature": "fixture"}
    client = FakeClient(
        [
            anthropic_reply(
                [
                    thinking,
                    {
                        "type": "tool_use",
                        "id": "a",
                        "name": "search",
                        "input": {"query": "fixture", "detail": "full"},
                    },
                ],
                "tool_use",
            ),
            anthropic_reply([{"type": "text", "text": f"Answer [[cite:{HANDLE}]]"}]),
        ]
    )
    mcp = FakeMCP()
    charges = []
    result = asyncio.run(
        run_task(
            AnthropicProvider(client, "claude-sonnet-5-5", "high"),
            mcp,
            TOOLS,
            "fixture question",
            "compact",
            4,
            5,
            charges.append,
        )
    )
    assert result.citations == [HANDLE]
    assert result.usage == Usage(20, 6, 40, 10)
    assert len(charges) == 2
    assert mcp.calls[0][1]["detail"] == "compact"
    assert "limit" not in mcp.calls[0][1]
    assert client.requests[0]["thinking"] == {"type": "adaptive"}
    assert client.requests[0]["output_config"] == {"effort": "high"}
    assert client.requests[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert client.requests[1]["messages"][1]["content"][0] == thinking
    assert result.tool_result_codepoints == len('{"complete":true,"text":"đúng"}')


def test_openai_fake_responses_loop_preserves_reasoning_and_usage():
    client = FakeClient(
        [
            {
                "model": "openai-test",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "id": "r", "summary": []},
                    {
                        "type": "function_call",
                        "call_id": "c",
                        "name": "search",
                        "arguments": '{"query":"fixture"}',
                    },
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 30},
                    "output_tokens": 10,
                },
            },
            {
                "model": "openai-test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": f"[[cite:{HANDLE}]]"}],
                    }
                ],
                "usage": {
                    "input_tokens": 150,
                    "input_tokens_details": {"cached_tokens": 60},
                    "output_tokens": 20,
                },
            },
        ]
    )
    result = asyncio.run(
        run_task(
            OpenAIProvider(client, "openai-test"),
            FakeMCP(),
            TOOLS,
            "fixture",
            "full",
            4,
            5,
            lambda usage: None,
        )
    )
    assert result.usage == Usage(160, 0, 90, 30)
    assert result.stop_reason == "final"
    assert result.citations == [HANDLE]
    assert client.requests[0]["store"] is False
    assert client.requests[1]["input"][1]["type"] == "reasoning"
    assert client.requests[1]["input"][-1]["type"] == "function_call_output"


@pytest.mark.parametrize("reason,expected", [("refusal", "refusal"), ("max_tokens", "output_cap")])
def test_refusal_and_truncation_are_not_successful_answers(reason, expected):
    client = FakeClient([anthropic_reply([], reason)])
    result = asyncio.run(
        run_task(
            AnthropicProvider(client, "claude-sonnet-5-5", "high"),
            FakeMCP(),
            TOOLS,
            "fixture",
            "full",
            2,
            3,
            lambda usage: None,
        )
    )
    assert result.stop_reason == expected
    assert result.citations == []


def test_max_turn_and_tool_cap_prevent_extra_requests():
    response = anthropic_reply(
        [{"type": "tool_use", "id": "a", "name": "search", "input": {"query": "fixture"}}],
        "tool_use",
    )
    client = FakeClient([response])
    result = asyncio.run(
        run_task(
            AnthropicProvider(client, "claude-sonnet-5-5", "high"),
            FakeMCP(),
            TOOLS,
            "fixture",
            "full",
            1,
            5,
            lambda usage: None,
        )
    )
    assert result.stop_reason == "max_turns"
    assert len(client.requests) == 1
    client = FakeClient([response])
    mcp = FakeMCP()
    result = asyncio.run(
        run_task(
            AnthropicProvider(client, "claude-sonnet-5-5", "high"),
            mcp,
            TOOLS,
            "fixture",
            "full",
            3,
            0,
            lambda usage: None,
        )
    )
    assert result.stop_reason == "max_tool_calls"
    assert not mcp.calls


def test_citation_parser_preserves_unicode_anchors_and_duplicate_suffixes():
    handle = "vn:vbpl:synthetic#dieu-1~2/khoan-1/diem-đ"
    assert parse_citations(f"[[cite:{handle}]]") == [handle]


@pytest.mark.parametrize("initially_blocked", [False, True])
def test_budget_is_checked_immediately_before_each_provider_request(initially_blocked):
    from trachuan_agenteval.providers import Call, Reply

    requests = []
    allowed = not initially_blocked

    async def request(*args):
        requests.append(1)
        return Reply(Usage(), "fake", [Call("call", "search", {})], "", "tools", [])

    async def call(*args):
        nonlocal allowed
        allowed = False
        return SimpleNamespace(text="{}", structured={})

    result = asyncio.run(
        run_task(
            SimpleNamespace(request=request, append=lambda *args: None),
            SimpleNamespace(call=call),
            TOOLS,
            "fixture",
            "full",
            3,
            5,
            lambda usage: None,
            can_request=lambda: allowed,
        )
    )
    assert len(requests) == (0 if initially_blocked else 1)
    assert result.stop_reason == "budget"
    assert not result.complete
