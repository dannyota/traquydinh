import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .usage import Price


def load_table() -> dict:
    return json.loads(Path(__file__).with_name("prices.json").read_text(encoding="utf-8"))


def load_prices(models: list[str]) -> dict[str, Price]:
    table = load_table()
    prices = {}
    for model in models:
        provider = "anthropic" if model == "claude-sonnet-5-5" else "openai"
        row = table[provider]
        try:
            values = [Decimal(row[k]) for k in ("input", "output", "cache_read", "cache_write")]
        except TypeError, InvalidOperation:
            raise ValueError("OpenAI price row must be filled before a paid run") from None
        if (
            row["model"] != model
            or not row["source_url"]
            or any(not v.is_finite() or v < 0 for v in values)
        ):
            raise ValueError("model price row is missing or invalid")
        prices[model] = Price(*values)
    return prices
