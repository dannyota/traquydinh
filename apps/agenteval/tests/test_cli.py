from decimal import Decimal
from pathlib import Path

import pytest

from trachuan_agenteval.cli import parse_args, prepare_plan
from trachuan_agenteval.pricing import load_prices

ROOT = Path(__file__).resolve().parents[3]


def test_cli_defaults_match_brief():
    args = parse_args(["run", "--query-embedder-url", "http://127.0.0.1:8091"])
    assert args.models == "claude-sonnet-5-5"
    assert args.modes == "full,compact,trimmed"
    assert args.runs == 2
    assert args.budget_usd == Decimal(60)
    plan = prepare_plan(args, ROOT)
    assert plan["planned_tasks"] == 390
    assert len(plan["case_ids"]) == 65
    assert "question" not in str(plan)


def test_openai_empty_price_row_refuses_paid_run_but_allows_dry_run():
    args = parse_args(
        [
            "run",
            "--models",
            "claude-sonnet-5-5,openai-test",
            "--dry-run",
            "--query-embedder-url",
            "http://127.0.0.1:8091",
        ]
    )
    assert prepare_plan(args, ROOT)["planned_tasks"] == 780
    with pytest.raises(ValueError, match="OpenAI price"):
        load_prices(["openai-test"])


@pytest.mark.parametrize(
    "arguments",
    [
        ["--cases", "0"],
        ["--runs", "0"],
        ["--budget-usd", "NaN"],
        ["--budget-usd", "-1"],
        ["--modes", "full,full"],
        ["--max-turns", "0"],
        ["--models", "claude-other"],
        ["--cases", "66"],
    ],
)
def test_invalid_plan_fails_before_stack(arguments):
    args = parse_args(["run", "--query-embedder-url", "http://127.0.0.1:8091", *arguments])
    with pytest.raises(ValueError):
        prepare_plan(args, ROOT)


def test_dry_run_starts_stack_discovers_both_servers_without_model_clients(
    tmp_path, monkeypatch, capsys
):
    import asyncio
    from contextlib import asynccontextmanager

    from trachuan_agenteval import cli

    events = []

    class FakeStack:
        def __init__(self, *args):
            self.scratch = tmp_path
            self.name = "trachuan_e2e_synthetic"
            self.endpoints = {"normal": ("normal", "google1"), "trimmed": ("trimmed", "google2")}

        def __enter__(self):
            events.append("start")
            return self

        def __exit__(self, *args):
            events.append("stop")

    async def authorize(*args):
        return object()

    class FakeMCP:
        async def tools(self):
            return [{"name": "fixture_tool", "inputSchema": {"type": "object"}}]

    @asynccontextmanager
    async def connect(server, token):
        events.append(server)
        yield FakeMCP()

    def forbidden(*args, **kwargs):
        pytest.fail("dry run must not construct model clients or require prices")

    monkeypatch.setattr(cli, "Stack", FakeStack)
    monkeypatch.setattr(cli, "authorize", authorize)
    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "AsyncAnthropic", forbidden)
    monkeypatch.setattr(cli, "AsyncOpenAI", forbidden)
    monkeypatch.setattr(cli, "load_prices", forbidden)
    out = tmp_path / "dry"
    args = parse_args(
        [
            "run",
            "--models",
            "claude-sonnet-5-5,openai-test",
            "--dry-run",
            "--query-embedder-url",
            "http://127.0.0.1:8091",
            "--out",
            str(out),
        ]
    )
    assert asyncio.run(cli.run(args, ROOT)) == 0
    assert events == ["start", "normal", "trimmed", "stop"]
    assert (out / "manifest.json").exists()
    assert not (out / "tasks.jsonl").exists()
    assert "fixture_tool" in capsys.readouterr().out


def test_at_most_one_openai_model_in_plan():
    args = parse_args(
        [
            "run",
            "--models",
            "openai-a,openai-b",
            "--dry-run",
            "--query-embedder-url",
            "http://127.0.0.1:8091",
        ]
    )
    with pytest.raises(ValueError):
        prepare_plan(args, ROOT)


def test_password_configuration_error_names_variable_without_value(tmp_path, monkeypatch, capsys):
    from trachuan_agenteval import cli
    from trachuan_agenteval.stack import Stack

    async def run(*args):
        Stack(
            tmp_path,
            tmp_path,
            "http://127.0.0.1:8091",
            [29080, 29082, 29081, 29083],
            "postgres://admin:synthetic-password@127.0.0.1/postgres",
            "postgres://reader@127.0.0.1/{database}",
        )
        return 0

    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(cli.logging, "disable", lambda *args: None)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    monkeypatch.setattr(
        cli.sys, "argv", ["agenteval", "run", "--query-embedder-url", "http://127.0.0.1:8091"]
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    message = capsys.readouterr().err
    assert "CORPUS_TEST_ADMIN_URL" in message
    assert "PGPASSWORD" in message
    assert "synthetic-password" not in message
