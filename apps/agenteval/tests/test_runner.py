import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trachuan_agenteval.agent import Outcome
from trachuan_agenteval.runner import execute
from trachuan_agenteval.usage import Budget, Price, Usage


@pytest.mark.parametrize("input_tokens", [500_000, 600_000])
def test_shared_budget_stops_inside_task_before_another_request(tmp_path, input_tokens):
    from trachuan_agenteval.providers import Call, Reply

    requests = []
    tools = []

    async def request(*args):
        requests.append(1)
        return Reply(Usage(input_tokens), "fake", [Call("call", "search", {})], "", "tools", [])

    async def call(*args):
        tools.append(1)
        return SimpleNamespace(text="{}", structured={})

    provider = SimpleNamespace(request=request, append=lambda *args: None)
    mcp = SimpleNamespace(call=call)
    matcher = SimpleNamespace(score=lambda *args: {"matched": 0, "expected": 1})
    summary = asyncio.run(
        execute(
            [{"id": "fixture", "question": "never write this question"}],
            ["fake"],
            ["full", "compact"],
            2,
            {"fake": provider},
            {"full": (mcp, [{"name": "search"}]), "compact": (mcp, [{"name": "search"}])},
            {"fake": Price(2, 10, 0.1, 2.5)},
            Budget(Decimal("1")),
            matcher,
            tmp_path,
            4,
            10,
        )
    )
    assert len(requests) == 1
    assert not tools
    assert summary["status"] == "incomplete"
    assert Decimal(summary["cost_usd"]) == Decimal(input_tokens) * 2 / 1_000_000
    mode = summary["models"]["fake"]["modes"]["full"]
    assert mode["incomplete_tasks"] == 1
    assert mode["mean_summed_input_tokens"] is None
    assert summary["models"]["fake"]["gates"]["compact_vs_full"] == "unavailable"
    path = tmp_path / "tasks.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["stop_reason"] == "budget"
    assert records[0]["complete"] is False
    assert records[0]["summed_input_tokens"] == input_tokens
    assert "never write" not in path.read_text()


def test_provider_error_stops_scheduling_because_spend_may_be_unknown(tmp_path):
    async def task(*args, **kwargs):
        return Outcome(stop_reason="provider_error")

    matcher = SimpleNamespace(score=lambda *args: {"matched": 0, "expected": 1})
    result = asyncio.run(
        execute(
            [{"id": "fixture", "question": "fixture"}],
            ["fake"],
            ["full", "compact"],
            1,
            {"fake": object()},
            {"full": (object(), []), "compact": (object(), [])},
            {"fake": Price(2, 10, 0.1, 2.5)},
            Budget(Decimal(60)),
            matcher,
            tmp_path,
            4,
            10,
            task=task,
        )
    )
    assert result["scheduled_tasks"] == 1
    assert result["status"] == "incomplete"
