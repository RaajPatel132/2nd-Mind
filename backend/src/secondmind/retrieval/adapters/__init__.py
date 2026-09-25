"""Postgres adapter for recall: the retrieval tools (one SQL query each, read-only and
workspace-scoped) and the conversation index."""

from secondmind.retrieval.adapters.tables import (
    WORKSPACE_OWNED_RECALL_TABLES,
    ConversationKeyRow,
)

__all__ = ["WORKSPACE_OWNED_RECALL_TABLES", "ConversationKeyRow"]
