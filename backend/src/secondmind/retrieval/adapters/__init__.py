"""Postgres adapter for recall: the retrieval tools (one SQL query each, read-only and
workspace-scoped) and the conversation index."""

from secondmind.retrieval.adapters.conversation import SqlConversationStore
from secondmind.retrieval.adapters.sql import INVERSE, SYMMETRIC, SqlRecallStore, tsquery_text
from secondmind.retrieval.adapters.tables import (
    WORKSPACE_OWNED_RECALL_TABLES,
    ConversationKeyRow,
)

__all__ = [
    "INVERSE",
    "SYMMETRIC",
    "WORKSPACE_OWNED_RECALL_TABLES",
    "ConversationKeyRow",
    "SqlConversationStore",
    "SqlRecallStore",
    "tsquery_text",
]
