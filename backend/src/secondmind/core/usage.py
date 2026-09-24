"""Normalised token usage and cost, the same shape for every provider (FR-14.3)."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from secondmind.core.money import UsdAmount, usd


class Usage(BaseModel):
    """Usage of one model call.

    ``input_tokens`` counts *uncached* input only; cached reads are in ``cached_input_tokens``.
    Adapters normalise to this split so cost is always
    ``input * p_in + cached * p_cached + output * p_out`` from the price table.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: UsdAmount
    latency_ms: int = Field(ge=0)
    price_version: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.cached_input_tokens + self.output_tokens


class UsageTotals(BaseModel):
    """Sum of usage across a turn's model calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: UsdAmount = Decimal(0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.cached_input_tokens + self.output_tokens

    def add(self, usage: Usage) -> "UsageTotals":
        return UsageTotals(
            input_tokens=self.input_tokens + usage.input_tokens,
            cached_input_tokens=self.cached_input_tokens + usage.cached_input_tokens,
            output_tokens=self.output_tokens + usage.output_tokens,
            cost_usd=usd(self.cost_usd + usage.cost_usd),
        )
