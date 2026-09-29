"""Quota (FR-12.1/12.2, ADR-0032): what's left of a person's lifetime allowance, in dollars of
model spend, by tier. ``used`` is the sum of the person's usage ledger across their
workspaces; every call counts at its real cost, embeddings included, except system usage
(background indexing and housekeeping), which the app pays for. Charged tokens stay on every
call as information (ADR-0030), shown next to the dollars."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from secondmind.core import Tier, UsdAmount, usd


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """Dollar limits per tier, from ``QUOTA_USD_<TIER>``."""

    guest: Decimal
    standard: Decimal
    premium: Decimal

    def for_tier(self, tier: Tier) -> Decimal:
        return {Tier.GUEST: self.guest, Tier.STANDARD: self.standard, Tier.PREMIUM: self.premium}[
            tier
        ]


class QuotaUsage(BaseModel):
    """A person's quota as it stands now."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: Tier
    limit_usd: UsdAmount
    used_usd: UsdAmount
    remaining_usd: UsdAmount
    used_tokens: int = Field(ge=0, description="Charged (weighted) tokens, as information.")


@dataclass(frozen=True, slots=True)
class Spent:
    usd: Decimal
    tokens: int


class LedgerReader(Protocol):
    async def spent(self, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID]) -> Spent:
        """What the person was charged in these workspaces (system usage left out)."""
        ...


class Quotas:
    def __init__(self, ledger: LedgerReader, limits: QuotaLimits) -> None:
        self._ledger = ledger
        self._limits = limits

    @property
    def limits(self) -> QuotaLimits:
        return self._limits

    async def usage(
        self, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID], tier: Tier
    ) -> QuotaUsage:
        limit = usd(self._limits.for_tier(tier))
        spent = await self._ledger.spent(user_id, workspace_ids)
        used = usd(spent.usd)
        return QuotaUsage(
            tier=tier,
            limit_usd=limit,
            used_usd=used,
            remaining_usd=max(Decimal(0), usd(limit - used)),
            used_tokens=spent.tokens,
        )
