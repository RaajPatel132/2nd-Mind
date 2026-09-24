"""The workspace scope every data access needs (NFR-2.1)."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """One isolated memory, plus the user acting in it.

    Repositories cannot be constructed without one, and the database adapter turns it into the
    per-transaction ``app.workspace_id`` setting that the RLS policies filter on.
    """

    workspace_id: uuid.UUID
    user_id: uuid.UUID
