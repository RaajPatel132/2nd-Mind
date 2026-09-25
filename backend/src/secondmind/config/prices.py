"""Model price table from ``config/prices.yaml`` (FR-14.5): versioned with the code, so the
cost shown for a turn is reproducible.

The table also names a **baseline** model. Quota is counted in baseline tokens: a call's tokens
are charged at its model's *weight*, the model's blended price over the baseline's, so the
metered number tracks real cost (PRD §7.12) and a bigger model uses the allowance faster.
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from secondmind.config.routing import FAKE_PROVIDER, ModelRef
from secondmind.core import ConfigError, usd

PER_MILLION = Decimal(1_000_000)
# The blend behind a weight: three input tokens to every output token.
BLEND_INPUT_PER_OUTPUT = Decimal(3)
WEIGHT_PLACES = Decimal("0.0001")

Price = Annotated[Decimal, Field(ge=0, decimal_places=6)]


class ModelPrice(BaseModel):
    """USD per 1M tokens."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: Price
    cached_input: Price
    output: Price


class PriceTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    currency: str = "USD"
    baseline: str = Field(description="provider:model that one quota token is worth")
    models: dict[str, dict[str, ModelPrice]]

    @property
    def baseline_ref(self) -> ModelRef:
        return ModelRef.parse(self.baseline)

    def price_for(self, ref: ModelRef) -> ModelPrice:
        price = self.models.get(ref.provider, {}).get(ref.model)
        if price is None and ref.provider == FAKE_PROVIDER:
            # The fake provider standing in for a real model is priced as that model.
            price = self._stand_in_price(ref.model)
        if price is None:
            raise ConfigError(f"no price for {ref} in config/prices.yaml")
        return price

    def _stand_in_price(self, model: str) -> ModelPrice | None:
        found = [
            table[model]
            for provider, table in self.models.items()
            if provider != FAKE_PROVIDER and model in table
        ]
        return found[0] if len(found) == 1 else None

    def weight(self, ref: ModelRef) -> Decimal:
        """How many baseline tokens one of this model's tokens is charged as."""
        p, b = self.price_for(ref), self.price_for(self.baseline_ref)
        ratio = (BLEND_INPUT_PER_OUTPUT * p.input + p.output) / (
            BLEND_INPUT_PER_OUTPUT * b.input + b.output
        )
        return ratio.quantize(WEIGHT_PLACES, rounding=ROUND_HALF_UP)

    def charged_tokens(self, ref: ModelRef, total_tokens: int) -> int:
        """Quota tokens for a call of ``total_tokens`` on ``ref``."""
        charged = Decimal(total_tokens) * self.weight(ref)
        return int(charged.to_integral_value(rounding=ROUND_HALF_UP))

    def cost(
        self, ref: ModelRef, *, input_tokens: int, cached_input_tokens: int, output_tokens: int
    ) -> Decimal:
        p = self.price_for(ref)
        total = (
            Decimal(input_tokens) * p.input
            + Decimal(cached_input_tokens) * p.cached_input
            + Decimal(output_tokens) * p.output
        ) / PER_MILLION
        return usd(total)

    def require(self, refs: set[ModelRef]) -> None:
        missing = sorted(str(r) for r in refs if not self._has_price(r))
        if missing:
            raise ConfigError(
                "config/prices.yaml has no price for routed model(s): " + ", ".join(missing)
            )

    def _has_price(self, ref: ModelRef) -> bool:
        try:
            self.price_for(ref)
        except ConfigError:
            return False
        return True


def read_price_table(path: Path) -> PriceTable:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        table = PriceTable.model_validate(raw)
    except FileNotFoundError as exc:
        raise ConfigError(f"price table not found: {path}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"invalid price table {path}:\n{exc}") from exc
    if table.currency != "USD":
        raise ConfigError(f"{path}: only USD prices are supported")
    try:
        baseline = table.baseline_ref
    except ValueError as exc:
        raise ConfigError(f"{path}: baseline: {exc}") from exc
    if not table._has_price(baseline):
        raise ConfigError(f"{path}: the baseline {baseline} has no price")
    base = table.price_for(baseline)
    if BLEND_INPUT_PER_OUTPUT * base.input + base.output <= 0:
        raise ConfigError(f"{path}: the baseline {baseline} must have a non-zero price")
    return table
