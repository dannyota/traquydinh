import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .usage import Usage, normalize_usage


def translate_tools(tools: list[dict], provider: str) -> list[dict]:
    translated = []
    for tool in tools:
        if provider == "anthropic":
            translated.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": deepcopy(tool["inputSchema"]),
                }
            )
        elif provider == "openai":
            translated.append(
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": deepcopy(tool["inputSchema"]),
                    "strict": False,
                }
            )
        else:
            raise ValueError("unknown provider")
    if provider == "anthropic" and translated:
        translated[-1]["cache_control"] = {"type": "ephemeral"}
    return translated


@dataclass
class Call:
    id: str
    name: str
    arguments: dict | None


@dataclass
class Reply:
    usage: Usage
    model: str
    calls: list[Call]
    text: str
    stop: str
    content: list[dict]


class AnthropicProvider:
    def __init__(self, client: Any, model: str, effort: str, max_output: int = 8192) -> None:
        self.client, self.model, self.effort, self.max_output = client, model, effort, max_output

    async def request(self, system: str, history: list[dict], tools: list[dict]) -> Reply:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=translate_tools(tools, "anthropic"),
            messages=deepcopy(history),
        )
        raw = response.model_dump(mode="json", exclude_none=True)
        usage = normalize_usage("anthropic", raw.get("usage", {}))
        content = raw["content"]
        calls = [Call(c["id"], c["name"], c["input"]) for c in content if c["type"] == "tool_use"]
        text = "\n".join(c["text"] for c in content if c["type"] == "text")
        reason = raw["stop_reason"]
        stop = {
            "end_turn": "final",
            "refusal": "refusal",
            "max_tokens": "output_cap",
            "tool_use": "tools",
        }.get(reason, "provider_stop")
        return Reply(usage, raw["model"], calls, text, stop, content)

    def append(self, history: list[dict], reply: Reply, results: list[tuple[Call, str]]) -> None:
        history.append({"role": "assistant", "content": reply.content})
        history.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call.id, "content": text}
                    for call, text in results
                ],
            }
        )


class OpenAIProvider:
    def __init__(self, client: Any, model: str, max_output: int = 8192) -> None:
        self.client, self.model, self.max_output = client, model, max_output

    async def request(self, system: str, history: list[dict], tools: list[dict]) -> Reply:
        response = await self.client.responses.create(
            model=self.model,
            instructions=system,
            input=deepcopy(history),
            tools=translate_tools(tools, "openai"),
            max_output_tokens=self.max_output,
            store=False,
            include=["reasoning.encrypted_content"],
        )
        raw = response.model_dump(mode="json", exclude_none=True)
        usage = normalize_usage("openai", raw.get("usage", {}))
        content = raw["output"]
        calls = []
        for item in content:
            if item["type"] == "function_call":
                try:
                    arguments = json.loads(item["arguments"])
                except ValueError, TypeError:
                    arguments = None
                calls.append(Call(item["call_id"], item["name"], arguments))
        parts = [
            part
            for item in content
            if item["type"] == "message"
            for part in item.get("content", [])
        ]
        text = "\n".join(p["text"] for p in parts if p["type"] == "output_text")
        if any(p["type"] == "refusal" for p in parts):
            stop = "refusal"
        elif raw.get("status") != "completed":
            stop = "output_cap" if raw.get("status") == "incomplete" else "provider_stop"
        else:
            stop = "tools" if calls else "final"
        return Reply(usage, raw["model"], calls, text, stop, content)

    def append(self, history: list[dict], reply: Reply, results: list[tuple[Call, str]]) -> None:
        history.extend(reply.content)
        history.extend(
            {"type": "function_call_output", "call_id": call.id, "output": text}
            for call, text in results
        )
