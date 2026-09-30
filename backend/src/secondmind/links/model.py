"""What is kept about a saved link (S4.7): one ``LinkSource`` per resource item made from a URL.

The item holds what the person said (``user_stated``) and, once the page is read, a title, summary
and tags (``content_derived``). The source records where it came from and how the read went, so
the glass box can say what happened and the item sheet can offer "Add the text".
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FetchStatus(StrEnum):
    PENDING = "pending"  # saved; the worker hasn't read it yet
    FULL = "full"  # main text found: metadata, summary, chunks
    PARTIAL = "partial"  # paywall, script-only, blocked, too large or short, a PDF: what there is
    FAILED = "failed"  # DNS, connection or timeout errors, or a 5xx after one retry
    REFUSED = "refused"  # a fetch-safety rule or a limit: no request was made


class LinkKind(StrEnum):
    ARTICLE = "article"
    VIDEO = "video"
    LINK = "link"


class LinkSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    workspace_id: uuid.UUID
    item_id: uuid.UUID
    turn_id: uuid.UUID  # the turn that saved it: everything the read writes goes on its log
    url: str
    canonical_url: str
    kind: LinkKind
    fetch_status: FetchStatus = FetchStatus.PENDING
    fetch_reason: str | None = None
    site: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    description: str | None = None
    word_count: int | None = None
    chunk_count: int = 0
    final_host: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    bytes_read: int | None = None
    redirects: int | None = None
    extraction_method: str | None = None
    channel: str | None = None
    duration_s: int | None = None
    thumbnail_url: str | None = None
    fetched_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


# What the person is told for each outcome (the Trail's `fetch` step, FR-4.6, and the reply).
PENDING_REPLY = "Saved the link. Reading it now."


class ChunkRow(BaseModel):
    """One passage of a saved page, as stored in ``memory_keys`` with the ``chunk`` kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    position: int
    text: str
    content_hash: str
    embedding: list[float] | None = None
    embedding_model: str | None = None
