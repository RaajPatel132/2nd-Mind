"""Recall tables (S3.2). ``conversation_keys`` indexes what was said in past turns, so "the books
you suggested last week" can be found; it is workspace-owned and protected by RLS (see the 0004
migration). It uses the same text-search config and embedding column as ``memory_keys``."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from secondmind.memory.adapters import EMBED_DIMENSIONS, Base, Vector


class ConversationKeyRow(Base):
    __tablename__ = "conversation_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(16))
    seq: Mapped[int] = mapped_column(Integer)
    # When it was said (the turn's start), so a window filter needs no join.
    said_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["turn_id", "workspace_id"], ["turns.id", "turns.workspace_id"], ondelete="CASCADE"
        ),
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        UniqueConstraint("turn_id", "role", "seq"),
        Index("ix_conversation_keys_tsv", "tsv", postgresql_using="gin"),
        Index("ix_conversation_keys_workspace_id_said_at", "workspace_id", "said_at"),
        Index("ix_conversation_keys_workspace_id_content_hash", "workspace_id", "content_hash"),
    )


WORKSPACE_OWNED_RECALL_TABLES = ("conversation_keys",)
