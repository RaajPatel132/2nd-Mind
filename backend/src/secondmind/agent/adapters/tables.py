"""Turn tables. Both are workspace-owned and protected by RLS (see the migration)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from secondmind.memory.adapters import Base


class TurnRow(Base):
    __tablename__ = "turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    input: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_hash: Mapped[str] = mapped_column(String(64))
    prompt_versions: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    models: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cached_input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), server_default="0")
    charged_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    trace_status: Mapped[str] = mapped_column(String(16), server_default="disabled")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    event_count: Mapped[int] = mapped_column(Integer, server_default="0")
    kind: Mapped[str] = mapped_column(String(16), server_default="user")
    parent_turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="status"),
        CheckConstraint("kind IN ('user', 'undo', 'confirm', 'edit', 'system')", name="kind"),
        CheckConstraint(
            "trace_status IN ('recorded', 'unavailable', 'disabled')", name="trace_status"
        ),
        Index("ix_turns_workspace_id_id", "workspace_id", "id"),
    )


class TurnEventRow(Base):
    __tablename__ = "turn_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    v: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # An event can never claim a different workspace than its turn.
        ForeignKeyConstraint(
            ["turn_id", "workspace_id"], ["turns.id", "turns.workspace_id"], ondelete="CASCADE"
        ),
        UniqueConstraint("turn_id", "seq"),
        Index("ix_turn_events_workspace_id", "workspace_id"),
    )
