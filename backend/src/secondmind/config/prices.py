"""Model price table from ``config/prices.yaml`` (FR-14.5): versioned with the code, so the
cost shown for a turn is reproducible."""

from decimal import Decimal
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from secondmind.config.routing import ModelRef
from secondmind.core import ConfigError, usd

PER_MILLION = Decimal(1_000_000)

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
    models: dict[str, dict[str, ModelPrice]]

    def price_for(self, ref: ModelRef) -> ModelPrice:
        try:
            return self.models[ref.provider][ref.model]
        except KeyError as exc:
            raise ConfigError(f"no price for {ref} in config/prices.yaml") from exc

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
        missing = sorted(str(r) for r in refs if r.model not in self.models.get(r.provider, {}))
        if missing:
            raise ConfigError(
                "config/prices.yaml has no price for routed model(s): " + ", ".join(missing)
            )


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
    return table
