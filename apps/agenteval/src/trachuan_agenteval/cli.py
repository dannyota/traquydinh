import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import httpx
import httpx2
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from .oauth import authorize
from .pricing import load_prices, load_table
from .prompt import SYSTEM
from .providers import AnthropicProvider, OpenAIProvider
from .runner import execute
from .scoring import Matcher
from .stack import AdminURLPasswordError, Stack, validate_loopback
from .transport import connect
from .usage import Budget


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agenteval")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--models", default="claude-sonnet-5-5")
    run.add_argument("--modes", default="full,compact,trimmed")
    run.add_argument("--cases", default="all")
    run.add_argument("--runs", type=int, default=2)
    run.add_argument("--budget-usd", type=Decimal, default=Decimal(60))
    run.add_argument("--out", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--query-embedder-url", required=True)
    run.add_argument("--effort", choices=["low", "medium", "high", "max"], default="high")
    run.add_argument("--max-turns", type=int, default=16)
    run.add_argument("--max-tool-calls", type=int, default=48)
    run.add_argument("--max-output-tokens", type=int, default=8192)
    run.add_argument(
        "--ports",
        default="29080,29082,29081,29083",
        help="normal server, trimmed server, normal Google, trimmed Google",
    )
    return parser.parse_args(argv)


def prepare_plan(args: argparse.Namespace, root: Path) -> dict:
    models, modes = args.models.split(","), args.modes.split(",")
    if (
        not models
        or len(models) != len(set(models))
        or len(models) > 2
        or sum(m != "claude-sonnet-5-5" for m in models) > 1
        or any(not m.strip() or m != m.strip() for m in models)
        or any(m.startswith("claude-") and m != "claude-sonnet-5-5" for m in models)
    ):
        raise ValueError("select claude-sonnet-5-5 and at most one OpenAI model")
    if (
        not modes
        or len(modes) != len(set(modes))
        or any(m not in {"full", "compact", "trimmed"} for m in modes)
    ):
        raise ValueError("modes must be unique full, compact or trimmed values")
    if (
        not args.budget_usd.is_finite()
        or args.budget_usd <= 0
        or args.runs < 1
        or args.max_turns < 1
        or args.max_tool_calls < 1
        or args.max_output_tokens < 1
    ):
        raise ValueError("budget, runs and task caps must be positive")
    validate_loopback(args.query_embedder_url)
    cases = load_cases(root)
    count = len(cases) if args.cases == "all" else int(args.cases)
    if not 0 < count <= len(cases):
        raise ValueError("cases must select between 1 and the cohort size")
    return {
        "models": models,
        "modes": modes,
        "case_ids": [c["id"] for c in cases[:count]],
        "runs": args.runs,
        "planned_tasks": len(models) * len(modes) * count * args.runs,
        "budget_usd": str(args.budget_usd),
        "max_turns": args.max_turns,
        "max_tool_calls": args.max_tool_calls,
        "max_output_tokens": args.max_output_tokens,
        "effort": args.effort,
        "corpus_database": "corpus_vn_20261114_5",
        "budget_policy": (
            "Check the ceiling before every request; finish only the request in flight."
        ),
    }


def load_cases(root: Path) -> list[dict]:
    return json.loads(
        (root / "apps/corpus/internal/evaluation/testdata/vn/development.json").read_text(
            encoding="utf-8"
        )
    )


def repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "go.mod").is_file() and (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("run from an editable repository install")


async def run(args: argparse.Namespace, root: Path) -> int:
    plan = prepare_plan(args, root)
    models, modes = plan["models"], plan["modes"]
    prices = {} if args.dry_run else load_prices(models)
    keys = {}
    if not args.dry_run:
        for model in models:
            name = "ANTHROPIC_API_KEY" if model == "claude-sonnet-5-5" else "OPENAI_API_KEY"
            if not os.environ.get(name):
                raise ValueError(name + " is required in the process environment")
            keys[model] = os.environ[name]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(4).hex()
    out = (args.out or root / "work/agenteval" / run_id).resolve()
    out.mkdir(parents=True, exist_ok=False)
    ports = [int(p) for p in args.ports.split(",")]
    admin = os.environ.get("CORPUS_TEST_ADMIN_URL", "postgres://trachuan@127.0.0.1:25433/postgres")
    corpus = os.environ.get("AGENTEVAL_CORPUS_DATABASE_URL") or os.environ.get(
        "SERVER_CORPUS_DATABASE_URL"
    )
    with Stack(root, out, args.query_embedder_url, ports, admin, corpus) as stack:
        async with AsyncExitStack() as resources:
            http = await resources.enter_async_context(
                httpx.AsyncClient(trust_env=False, timeout=15)
            )
            connections, discovered = {}, {}
            for name, (server, google) in stack.endpoints.items():
                token = await authorize(http, server, google)
                mcp = await resources.enter_async_context(connect(server, token))
                tools = await mcp.tools()
                discovered[name] = tools
                connections[name] = (mcp, tools)
            plan["tools"] = {name: [t["name"] for t in tools] for name, tools in discovered.items()}
            fixture_dir = root / "apps/corpus/internal/evaluation/testdata/vn"
            manifest = plan | {
                "run_id": run_id,
                "dry_run": args.dry_run,
                "prices": load_table(),
                "system_prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
                "fixture_sha256": {
                    name: hashlib.sha256((fixture_dir / name).read_bytes()).hexdigest()
                    for name in ("development.json", "identity-map.json")
                },
                "sdk_versions": {name: version(name) for name in ("anthropic", "openai", "mcp")},
                "tool_schema_sha256": {
                    name: hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()
                    for name, tools in discovered.items()
                },
                "temporary_auth_database": stack.name,
                "ports": ports,
            }
            (out / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(plan, indent=2))
            if args.dry_run:
                return 0
            providers = {}
            for model in models:
                provider_http = await resources.enter_async_context(
                    httpx2.AsyncClient(trust_env=False, follow_redirects=False, timeout=180)
                )
                if model == "claude-sonnet-5-5":
                    client = await resources.enter_async_context(
                        AsyncAnthropic(
                            api_key=keys[model],
                            base_url="https://api.anthropic.com",
                            max_retries=0,
                            timeout=180,
                            http_client=provider_http,
                            open_telemetry=False,
                        )
                    )
                    providers[model] = AnthropicProvider(
                        client, model, args.effort, args.max_output_tokens
                    )
                else:
                    client = await resources.enter_async_context(
                        AsyncOpenAI(
                            api_key=keys[model],
                            base_url="https://api.openai.com/v1",
                            max_retries=0,
                            timeout=180,
                            http_client=provider_http,
                        )
                    )
                    providers[model] = OpenAIProvider(client, model, args.max_output_tokens)
            arms = {
                mode: connections["trimmed" if mode == "trimmed" else "normal"] for mode in modes
            }
            cases = load_cases(root)[: len(plan["case_ids"])]
            summary = await execute(
                cases,
                models,
                modes,
                args.runs,
                providers,
                arms,
                prices,
                Budget(args.budget_usd),
                Matcher(stack.scratch / "agentmatch"),
                out,
                args.max_turns,
                args.max_tool_calls,
            )
            print(json.dumps({"status": summary["status"], "cost_usd": summary["cost_usd"]}))
            return 0 if summary["status"] == "complete" else 2


def main() -> None:
    logging.disable(logging.CRITICAL)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    try:
        code = asyncio.run(run(parse_args(), repository_root()))
    except KeyboardInterrupt, SystemExit:
        raise
    except AdminURLPasswordError as error:
        print(str(error), file=sys.stderr)
        code = 1
    except Exception:
        print(
            "agenteval failed; check local prerequisites, arguments and price configuration",
            file=sys.stderr,
        )
        code = 1
    raise SystemExit(code)
