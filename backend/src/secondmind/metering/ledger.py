"""Usage ledger entries (FR-12.1). Quotas and spend caps read this ledger (ADR-0032).

``cost_usd`` is what the quota counts. ``charged_tokens`` is the call's tokens at its model's
weight against the baseline (ADR-0030), kept as information. ``system`` marks a call the app
paid for (background indexing, housekeeping), which is on the ledger so spend caps see every
dollar but isn't charged to the person."""

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
    system: bool = False

    @classmethod
    def from_model_call(
        cls,
        *,
        workspace_id: uuid.UUID,
        turn_id: uuid.UUID,
        event: ModelCallEvent,
        system: bool = False,
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
            system=system,
        )
