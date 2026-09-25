"""Postgres adapter for the memory store (engine, RLS-scoped sessions, role bootstrap, memory
tables), plus an in-memory store with the same contract for tests and evals."""

from secondmind.core import WorkspaceScope
from secondmind.memory.adapters.bootstrap import ensure_app_role
from secondmind.memory.adapters.db import NAMING, SCHEMA_HEAD, Base, Database, RoleCheck
from secondmind.memory.adapters.inmemory import InjectedFaultError, InMemoryMemory
from secondmind.memory.adapters.store import SqlMemoryStore, embedding_dimensions
from secondmind.memory.adapters.tables import (
    EMBED_DIMENSIONS,
    WORKSPACE_OWNED_MEMORY_TABLES,
    CategoryRow,
    EntityRelationRow,
    EntityRow,
    ItemAccessRow,
    MemoryEntityRow,
    MemoryItemRow,
    MemoryKeyRow,
    MemoryLinkRow,
    TriggerRow,
    Vector,
)


def sql_memory(db: Database) -> "SqlMemoryFactory":
    """A store factory for :class:`secondmind.memory.Memory` over Postgres."""
    return SqlMemoryFactory(db)


class SqlMemoryFactory:
    def __init__(self, db: Database) -> None:
        self._db = db

    def __call__(self, scope: WorkspaceScope) -> SqlMemoryStore:
        return SqlMemoryStore(self._db, scope)


__all__ = [
    "EMBED_DIMENSIONS",
    "NAMING",
    "SCHEMA_HEAD",
    "WORKSPACE_OWNED_MEMORY_TABLES",
    "Base",
    "CategoryRow",
    "Database",
    "EntityRelationRow",
    "EntityRow",
    "InMemoryMemory",
    "InjectedFaultError",
    "ItemAccessRow",
    "MemoryEntityRow",
    "MemoryItemRow",
    "MemoryKeyRow",
    "MemoryLinkRow",
    "RoleCheck",
    "SqlMemoryFactory",
    "TriggerRow",
    "Vector",
    "embedding_dimensions",
    "ensure_app_role",
    "sql_memory",
]
