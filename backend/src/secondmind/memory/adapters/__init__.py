"""Postgres adapter for the memory store: engine, RLS-scoped sessions, role bootstrap."""

from secondmind.memory.adapters.bootstrap import ensure_app_role
from secondmind.memory.adapters.db import NAMING, SCHEMA_HEAD, Base, Database, RoleCheck

__all__ = ["NAMING", "SCHEMA_HEAD", "Base", "Database", "RoleCheck", "ensure_app_role"]
