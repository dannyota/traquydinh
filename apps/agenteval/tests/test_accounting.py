import json
from decimal import Decimal

import pytest

from trachuan_agenteval.reporting import summarize, write_record
from trachuan_agenteval.usage import Budget, Price, Usage, normalize_usage


def test_usage_sums_all_cache_fields_and_requests():
    first = normalize_usage(
        "anthropic",
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 300,
            "output_tokens": 20,
        },
    )
    second = normalize_usage(
        "openai",
        {
            "input_tokens": 500,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 40},
            "output_tokens": 30,
        },
    )
    assert first + second == Usage(360, 90, 500, 50)
    assert (first + second).summed_input == 950
    assert Price(2, 10, 0.1, 2.5).cost(first) == Decimal("0.000555")


def test_unknown_usage_is_not_silently_zero():
    with pytest.raises(ValueError):
        normalize_usage("openai", {})


def test_budget_finishes_inflight_but_stops_new_tasks():
    budget = Budget(Decimal("0.001"))
    assert budget.can_schedule
    budget.charge(Decimal("0.0011"))
    assert not budget.can_schedule
    budget.charge(Decimal("0.0002"))
    assert budget.spent == Decimal("0.0013")


def row(mode, cited, tokens, case="fixture", run=1):
    return {
        "case_id": case,
        "run": run,
        "model": "fake",
        "mode": mode,
        "expected_cited": cited,
        "summed_input_tokens": tokens,
        "tool_calls": 2,
        "tool_result_codepoints": 200,
        "wall_seconds": 1,
        "usage": {"uncached_input": tokens, "cache_write": 0, "cache_read": 0, "output": 2},
        "stop_reason": "final",
        "complete": True,
    }


def test_summary_gates_use_means_and_strict_token_reduction():
    records = [row("full", 2, 100), row("compact", 2, 80), row("trimmed", 2, 80)]
    summary = summarize(records, ["fake"], ["full", "compact", "trimmed"], 1)
    assert summary["fake"]["gates"] == {
        "compact_vs_full": "pass",
        "trimmed_vs_compact": "fail",
    }
    records[1]["expected_cited"] = 1
    assert (
        summarize(records, ["fake"], ["full", "compact", "trimmed"], 1)["fake"]["gates"][
            "compact_vs_full"
        ]
        == "fail"
    )


def test_incomplete_or_unpaired_summary_cannot_pass():
    records = [row("full", 2, 100), row("compact", 2, 80, case="other")]
    assert (
        summarize(records, ["fake"], ["full", "compact"], 1)["fake"]["gates"]["compact_vs_full"]
        == "unavailable"
    )


def test_jsonl_allowlist_discards_question_answer_and_secret(tmp_path):
    record = row("full", 1, 20)
    record.update(question="private question", answer="private answer", api_key="synthetic")
    path = tmp_path / "tasks.jsonl"
    write_record(path, record)
    loaded = json.loads(path.read_text())
    assert loaded["case_id"] == "fixture"
    assert not {"question", "answer", "api_key"} & loaded.keys()
    assert "private" not in path.read_text()


def test_duplicate_task_keys_cannot_make_gate_available():
    records = [
        row("full", 2, 100),
        row("full", 2, 100),
        row("compact", 2, 80),
        row("compact", 2, 80),
    ]
    assert (
        summarize(records, ["fake"], ["full", "compact"], 2)["fake"]["gates"]["compact_vs_full"]
        == "unavailable"
    )


def test_gate_means_exclude_incomplete_tasks_and_report_counts():
    complete = row("compact", 2, 80)
    interrupted = row("compact", 0, 10_000, case="interrupted")
    interrupted.update(complete=False, stop_reason="budget")
    summary = summarize([complete, interrupted], ["fake"], ["compact"], 2)
    mode = summary["fake"]["modes"]["compact"]
    assert mode["tasks"] == 2
    assert mode["completed_tasks"] == 1
    assert mode["incomplete_tasks"] == 1
    assert mode["mean_expected_cited"] == 2
    assert mode["mean_summed_input_tokens"] == 80
    assert mode["mean_usage"]["uncached_input"] == 80
    assert summary["fake"]["gates"]["compact_vs_full"] == "unavailable"
