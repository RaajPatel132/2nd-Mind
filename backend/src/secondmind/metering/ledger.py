"""Usage ledger entries (FR-12.1). Quota enforcement reads this ledger from S4.

``charged_tokens`` is what the quota counts: the call's tokens at its model's weight against
the baseline (ADR-0030), fixed when the call is recorded."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from secondmind.core import ModelCallEvent, UsdAmount


class LedgerEntry(BaseModel):
    """Tokens and cost of one model call, charged to the turn and the workspace's owner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: uuid.UUID
    turn_id: uuid.UUID
    step: str
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: UsdAmount
    charged_tokens: int = Field(ge=0)
    price_version: str

    @classmethod
    def from_model_call(
        cls, *, workspace_id: uuid.UUID, turn_id: uuid.UUID, event: ModelCallEvent
    ) -> "LedgerEntry":
        u = event.usage
        return cls(
            workspace_id=workspace_id,
            turn_id=turn_id,
            step=event.step,
            provider=u.provider,
            model=u.model,
            input_tokens=u.input_tokens,
            cached_input_tokens=u.cached_input_tokens,
            output_tokens=u.output_tokens,
            cost_usd=u.cost_usd,
            charged_tokens=u.charged,
            price_version=u.price_version,
        )
