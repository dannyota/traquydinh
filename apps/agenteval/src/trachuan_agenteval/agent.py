import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .prompt import SYSTEM
from .scoring import Evidence, parse_citations
from .usage import Usage


@dataclass
class Outcome:
    usage: Usage = field(default_factory=Usage)
    citations: list[str] = field(default_factory=list)
    model_versions: list[str] = field(default_factory=list)
    evidence: Evidence = field(default_factory=Evidence)
    tool_calls: int = 0
    tool_result_codepoints: int = 0
    wall_seconds: float = 0
    stop_reason: str = "max_turns"

    @property
    def complete(self) -> bool:
        return self.stop_reason in {"final", "refusal"}


async def run_task(
    provider: Any,
    mcp: Any,
    tools: list[dict],
    question: str,
    mode: str,
    max_turns: int,
    max_tool_calls: int,
    on_usage: Callable[[Usage], None],
    clock: Callable[[], float] = time.monotonic,
    can_request: Callable[[], bool] | None = None,
) -> Outcome:
    started = clock()
    outcome = Outcome()
    history = [{"role": "user", "content": question}]
    names = {tool["name"] for tool in tools}
    for _ in range(max_turns):
        if can_request is not None and not can_request():
            outcome.stop_reason = "budget"
            break
        try:
            reply = await provider.request(SYSTEM, history, tools)
        except Exception:
            outcome.stop_reason = "provider_error"
            break
        on_usage(reply.usage)
        outcome.usage += reply.usage
        if reply.model not in outcome.model_versions:
            outcome.model_versions.append(reply.model)
        if reply.stop != "tools":
            outcome.stop_reason = reply.stop
            if reply.stop == "final":
                outcome.citations = parse_citations(reply.text)
            break
        if can_request is not None and not can_request():
            outcome.stop_reason = "budget"
            break
        results = []
        for call in reply.calls:
            if outcome.tool_calls >= max_tool_calls:
                outcome.stop_reason = "max_tool_calls"
                break
            outcome.tool_calls += 1
            if call.name not in names or not isinstance(call.arguments, dict):
                text = '{"error":"invalid_tool_call"}'
            else:
                arguments = dict(call.arguments)
                if call.name == "search":
                    arguments["detail"] = "full" if mode == "full" else "compact"
                try:
                    result = await mcp.call(call.name, arguments)
                    text = result.text
                    outcome.evidence.add(call.name, arguments, result.structured)
                except Exception:
                    outcome.stop_reason = "tool_error"
                    break
            outcome.tool_result_codepoints += len(text)
            results.append((call, text))
        if outcome.stop_reason in {"max_tool_calls", "tool_error"}:
            break
        provider.append(history, reply, results)
    outcome.wall_seconds = clock() - started
    return outcome
