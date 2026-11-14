import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agent import run_task
from .reporting import summarize, write_record
from .usage import Budget, Price, Usage


async def execute(
    cases: list[dict],
    models: list[str],
    modes: list[str],
    runs: int,
    providers: dict[str, Any],
    connections: dict[str, tuple[Any, list[dict]]],
    prices: dict[str, Price],
    budget: Budget,
    matcher: Any,
    out: Path,
    max_turns: int,
    max_tool_calls: int,
    task: Callable = run_task,
) -> dict:
    records = []
    halted = False
    for case in cases:
        for run in range(1, runs + 1):
            for model in models:
                for mode in modes:
                    if halted or not budget.can_schedule:
                        halted = True
                        break
                    mcp, tools = connections[mode]
                    before = budget.spent

                    def charge(usage: Usage, price: Price = prices[model]) -> None:
                        budget.charge(price.cost(usage))

                    result = await task(
                        providers[model],
                        mcp,
                        tools,
                        case["question"],
                        mode,
                        max_turns,
                        max_tool_calls,
                        charge,
                        can_request=lambda: budget.can_schedule,
                    )
                    try:
                        score = matcher.score(case["id"], result.citations, result.evidence)
                    except Exception:
                        score = {"matched": 0, "expected": len(case.get("expected_citations", []))}
                        result.stop_reason = "scoring_error"
                    record = {
                        "case_id": case["id"],
                        "run": run,
                        "model": model,
                        "mode": mode,
                        "model_versions": result.model_versions,
                        "expected_cited": score["matched"],
                        "expected_total": score["expected"],
                        "citations": result.citations,
                        "usage": asdict(result.usage),
                        "summed_input_tokens": result.usage.summed_input,
                        "tool_calls": result.tool_calls,
                        "tool_result_codepoints": result.tool_result_codepoints,
                        "wall_seconds": result.wall_seconds,
                        "stop_reason": result.stop_reason,
                        "complete": result.complete,
                        "cost_usd": str(budget.spent - before),
                    }
                    write_record(out / "tasks.jsonl", record)
                    records.append(record)
                    if result.stop_reason in {"provider_error", "tool_error", "scoring_error"}:
                        halted = True
    planned = len(cases) * runs * len(models) * len(modes)
    complete = (
        len(records) == planned
        and all(r["complete"] for r in records)
        and budget.spent <= budget.cap
    )
    summary = {
        "status": "complete" if complete else "incomplete",
        "cost_usd": str(budget.spent),
        "budget_usd": str(budget.cap),
        "budget_exceeded": budget.spent > budget.cap,
        "planned_tasks": planned,
        "scheduled_tasks": len(records),
        "incomplete_tasks": sum(not r["complete"] for r in records),
        "models": summarize(records, models, modes, len(cases) * runs),
        "limitations": ["Development cohort reused; small sample; no superiority claim."],
    }
    if not complete and budget.spent > budget.cap:
        for group in summary["models"].values():
            group["gates"] = dict.fromkeys(group["gates"], "unavailable")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
