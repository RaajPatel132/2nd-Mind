"""SQL side of the persona: the copy function and the template's mark (S4.10, S4.11)."""

from secondmind.persona.adapters.store import SqlPersonaStore, workspace_tables

__all__ = ["SqlPersonaStore", "workspace_tables"]
