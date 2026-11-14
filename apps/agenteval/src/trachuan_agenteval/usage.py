from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Usage:
    uncached_input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0

    @property
    def summed_input(self) -> int:
        return self.uncached_input + self.cache_write + self.cache_read

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.uncached_input + other.uncached_input,
            self.cache_write + other.cache_write,
            self.cache_read + other.cache_read,
            self.output + other.output,
        )


def normalize_usage(provider: str, raw: dict) -> Usage:
    if not isinstance(raw.get("input_tokens"), int) or not isinstance(
        raw.get("output_tokens"), int
    ):
        raise ValueError("provider usage unavailable")
    if provider == "anthropic":
        usage = Usage(
            raw["input_tokens"],
            raw.get("cache_creation_input_tokens") or 0,
            raw.get("cache_read_input_tokens") or 0,
            raw["output_tokens"],
        )
    elif provider == "openai":
        details = raw.get("input_tokens_details") or {}
        read = details.get("cached_tokens") or 0
        write = details.get("cache_write_tokens") or 0
        usage = Usage(raw["input_tokens"] - read - write, write, read, raw["output_tokens"])
    else:
        raise ValueError("unknown provider")
    if any(
        type(n) is not int or n < 0
        for n in (
            usage.uncached_input,
            usage.cache_write,
            usage.cache_read,
            usage.output,
        )
    ):
        raise ValueError("invalid provider usage")
    return usage


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal

    def cost(self, usage: Usage) -> Decimal:
        return sum(
            Decimal(str(price)) * count
            for price, count in (
                (self.input, usage.uncached_input),
                (self.output, usage.output),
                (self.cache_read, usage.cache_read),
                (self.cache_write, usage.cache_write),
            )
        ) / Decimal(1_000_000)


@dataclass
class Budget:
    cap: Decimal
    spent: Decimal = Decimal(0)

    @property
    def can_schedule(self) -> bool:
        return self.spent < self.cap

    def charge(self, cost: Decimal) -> None:
        self.spent += cost
