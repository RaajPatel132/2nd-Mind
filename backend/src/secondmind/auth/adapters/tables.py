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
    # A persona template, and a copy of it, record the seed they hold (S4.10, S4.11).
    seed_id: Mapped[str | None] = mapped_column(Text)
    seed_version: Mapped[int | None] = mapped_column(Integer)
    seed_hash: Mapped[str | None] = mapped_column(Text)
    moved_days: Mapped[int | None] = mapped_column(Integer)
    # A guest's workspace whose content was emptied after GUEST_TTL_DAYS (S4.12).
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "kind IN ('private', 'guest', 'persona_copy', 'template', 'scratch')", name="kind"
        ),
        # Each account has exactly one private workspace, and at most one persona copy.
        Index(
            "uq_workspaces_owner_private",
            "owner_user_id",
            unique=True,
            postgresql_where=text("kind = 'private'"),
        ),
        Index(
            "uq_workspaces_owner_persona",
            "owner_user_id",
            unique=True,
            postgresql_where=text("kind = 'persona_copy'"),
        ),
        # A guest has at most one scratch memory.
        Index(
            "uq_workspaces_owner_scratch",
            "owner_user_id",
            unique=True,
            postgresql_where=text("kind = 'scratch'"),
        ),
        # One template per seed.
        Index(
            "uq_workspaces_template_seed",
            "seed_id",
            unique=True,
            postgresql_where=text("kind = 'template'"),
        ),
    )
