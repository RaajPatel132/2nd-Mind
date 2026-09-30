"""``link_sources`` (S4.7): where a saved link came from and how reading it went. Workspace-owned
and protected by RLS (see the 0006 migration)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from secondmind.memory.adapters import Base


class LinkSourceRow(Base):
    __tablename__ = "link_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    # The turn that saved the link: what the read writes goes on this turn's write log, so
    # undoing the turn removes it all.
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    fetch_status: Mapped[str] = mapped_column(String(16))
    fetch_reason: Mapped[str | None] = mapped_column(Text)
    site: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)
    word_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    final_host: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(128))
    bytes_read: Mapped[int | None] = mapped_column(Integer)
    redirects: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str | None] = mapped_column(String(32))
    channel: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "workspace_id"],
            ["memory_items.id", "memory_items.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["turn_id", "workspace_id"], ["turns.id", "turns.workspace_id"], ondelete="CASCADE"
        ),
        CheckConstraint("kind IN ('article', 'video', 'link')", name="kind"),
        CheckConstraint(
            "fetch_status IN ('pending', 'full', 'partial', 'failed', 'refused')",
            name="fetch_status",
        ),
        UniqueConstraint("workspace_id", "item_id"),
        Index("ix_link_sources_workspace_id_canonical_url", "workspace_id", "canonical_url"),
    )


WORKSPACE_OWNED_LINK_TABLES = ("link_sources",)
