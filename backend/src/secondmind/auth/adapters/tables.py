"""Identity tables. Not workspace-owned: they are what workspace scopes are resolved from."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from secondmind.memory.adapters import Base


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)
    # What the person may spend and pick (ADR-0032); changed only by the admin CLI.
    tier: Mapped[str] = mapped_column(String(16), server_default="standard")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        CheckConstraint("tier IN ('guest', 'standard', 'premium')", name="tier"),
    )


class TierChangeRow(Base):
    """Who changed whose tier, when, from what to what. The app role can read it, never write:
    only the admin CLI (running as the schema owner) adds rows."""

    __tablename__ = "tier_changes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    from_tier: Mapped[str] = mapped_column(String(16))
    to_tier: Mapped[str] = mapped_column(String(16))
    changed_by: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(Text)
    # Default reminder lead time (FR-3.9), used when a message doesn't give one.
    default_lead_minutes: Mapped[int] = mapped_column(Integer, server_default="1440")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("kind IN ('private', 'guest', 'persona_copy')", name="kind"),
        # Each account has exactly one private workspace.
        Index(
            "uq_workspaces_owner_private",
            "owner_user_id",
            unique=True,
            postgresql_where=text("kind = 'private'"),
        ),
    )
