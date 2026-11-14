import json
from pathlib import Path
from statistics import mean


def write_record(path: Path, record: dict) -> None:
    fields = {
        "case_id",
        "run",
        "model",
        "mode",
        "model_versions",
        "expected_cited",
        "expected_total",
        "citations",
        "usage",
        "summed_input_tokens",
        "tool_calls",
        "tool_result_codepoints",
        "wall_seconds",
        "stop_reason",
        "complete",
        "cost_usd",
    }
    safe = {key: value for key, value in record.items() if key in fields}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()


def summarize(records: list[dict], models: list[str], modes: list[str], expected: int) -> dict:
    summaries = {}
    for model in models:
        groups = {
            mode: [r for r in records if r["model"] == model and r["mode"] == mode]
            for mode in modes
        }
        metrics = {}
        for mode, rows in groups.items():
            unique = len({(r["case_id"], r["run"]) for r in rows}) == len(rows)
            completed = [r for r in rows if r["complete"]]
            metrics[mode] = {
                "tasks": len(rows),
                "completed_tasks": len(completed),
                "incomplete_tasks": len(rows) - len(completed),
                "complete": len(rows) == expected and unique and all(r["complete"] for r in rows),
            }
            for metric in (
                "expected_cited",
                "summed_input_tokens",
                "tool_calls",
                "tool_result_codepoints",
                "wall_seconds",
            ):
                metrics[mode]["mean_" + metric] = (
                    mean(r[metric] for r in completed) if completed else None
                )
            metrics[mode]["mean_usage"] = {
                name: mean(r["usage"][name] for r in completed) if completed else None
                for name in ("uncached_input", "cache_write", "cache_read", "output")
            }
        gates = {}
        for candidate, baseline in (("compact", "full"), ("trimmed", "compact")):
            a, b = groups.get(candidate, []), groups.get(baseline, [])
            paired = {(r["case_id"], r["run"]) for r in a} == {(r["case_id"], r["run"]) for r in b}
            available = (
                candidate in metrics
                and baseline in metrics
                and paired
                and metrics[candidate]["complete"]
                and metrics[baseline]["complete"]
            )
            verdict = "unavailable"
            if available:
                verdict = (
                    "pass"
                    if (
                        metrics[candidate]["mean_expected_cited"]
                        >= metrics[baseline]["mean_expected_cited"]
                        and metrics[candidate]["mean_summed_input_tokens"]
                        < metrics[baseline]["mean_summed_input_tokens"]
                    )
                    else "fail"
                )
            gates[candidate + "_vs_" + baseline] = verdict
        summaries[model] = {"modes": metrics, "gates": gates}
    return summaries
