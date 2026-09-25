"""Quota: how much of a user's lifetime token allowance is left (FR-12.5). Read only until S4,
which adds enforcement. ``used`` is the sum of the user's usage ledger across their
workspaces; every model call counts, embeddings included."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class Tier(StrEnum):
    GUEST = "guest"
    STANDARD = "standard"
    PREMIUM = "premium"


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """Token limits per tier, from ``QUOTA_TOKENS_<TIER>``."""

    guest: int
    standard: int
    premium: int

    def for_tier(self, tier: Tier) -> int:
        return {Tier.GUEST: self.guest, Tier.STANDARD: self.standard, Tier.PREMIUM: self.premium}[
            tier
        ]


class QuotaUsage(BaseModel):
    """A user's quota as it stands now."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: Tier
    limit_tokens: int = Field(ge=0)
    used_tokens: int = Field(ge=0)
    remaining_tokens: int = Field(ge=0)


class LedgerReader(Protocol):
    async def tokens_used(self, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID]) -> int:
        """Total tokens (input, cached input and output) the user was charged in these
        workspaces."""
        ...


class Quotas:
    def __init__(self, ledger: LedgerReader, limits: QuotaLimits) -> None:
        self._ledger = ledger
        self._limits = limits

    async def usage(self, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID]) -> QuotaUsage:
        # Every signed-in user is on the standard tier until accounts and tiers arrive (S6).
        tier = Tier.STANDARD
        limit = self._limits.for_tier(tier)
        used = await self._ledger.tokens_used(user_id, workspace_ids)
        return QuotaUsage(
            tier=tier,
            limit_tokens=limit,
            used_tokens=used,
            remaining_tokens=max(0, limit - used),
        )
