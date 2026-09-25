"""Usage ledger: every model call's tokens, against the turn and the workspace owner."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from secondmind.memory.adapters import Base


class UsageLedgerRow(Base):
    __tablename__ = "usage_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    step: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer)
    cached_input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    charged_tokens: Mapped[int] = mapped_column(Integer)
    price_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["turn_id", "workspace_id"], ["turns.id", "turns.workspace_id"], ondelete="CASCADE"
        ),
        Index("ix_usage_ledger_owner_user_id_created_at", "owner_user_id", "created_at"),
        Index("ix_usage_ledger_turn_id", "turn_id"),
        Index("ix_usage_ledger_workspace_id", "workspace_id"),
    )
