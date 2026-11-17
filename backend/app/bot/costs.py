"""Estimated cost of a bot turn, from the tokens the API reports (bot rule 10).

List prices in US dollars per million tokens, for prompts under 100K tokens (a bot turn is a few
thousand). They are an estimate for the turn log and the 1D meter, not the bill: check the
current prices when they change and update this table.
"""

import math
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True, slots=True)
class _Prices:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


# Cache reads cost a tenth of the input price; writes to the 5-minute cache, 1.25 times.
PRICES = {
    "claude-haiku-5-5": _Prices(Decimal("0.10"), Decimal("0.50"), Decimal("0.01"), Decimal("0.125")),
    "claude-sonnet-5-5": _Prices(Decimal("2.00"), Decimal("10.00"), Decimal("0.10"), Decimal("2.50")),
}


def estimate_microusd(model: str, usage: TokenUsage) -> int:
    """Millionths of a dollar: a price per million tokens is a price in microdollars per token."""
    prices = PRICES.get(model)
    if prices is None:
        return 0
    return math.ceil(
        usage.input_tokens * prices.input
        + usage.output_tokens * prices.output
        + usage.cache_read_tokens * prices.cache_read
        + usage.cache_write_tokens * prices.cache_write
    )
