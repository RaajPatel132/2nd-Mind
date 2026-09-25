"""Memory housekeeping jobs. Each change runs as a system turn through the turn runner, so it's
auditable and undoable like anything a user does (S2.9, S2.14)."""

import uuid
from dataclasses import dataclass
from typing import Any

from secondmind.agent import TurnRunner
from secondmind.auth import IdentityStore
from secondmind.core import WorkspaceScope
from secondmind.observability import get_logger

log = get_logger(__name__)

DEPS_KEY = "deps"
# How often the quick layer is tidied (the worker's cron schedule).
EXPIRE_QUICK_EVERY_MINUTES = 10


@dataclass(frozen=True, slots=True)
class JobDeps:
    """What memory jobs need, put into the arq context by the worker at start-up."""

    runner: TurnRunner
    identity: IdentityStore


def _deps(ctx: dict[str, Any]) -> JobDeps:
    deps = ctx.get(DEPS_KEY)
    if not isinstance(deps, JobDeps):
        raise RuntimeError("the worker started without its runtime")
    return deps


async def rerender_entity_keys(
    ctx: dict[str, Any], workspace_id: str, user_id: str, entity_ids: list[str]
) -> dict[str, str]:
    """Re-render the keys of items linked to renamed or relabelled entities (S2.14)."""
    deps = _deps(ctx)
    workspace = await deps.identity.get_workspace(uuid.UUID(workspace_id))
    if workspace is None:
        log.warning("job.rerender_skipped", reason="workspace gone")
        return {"status": "skipped"}
    scope = WorkspaceScope(workspace_id=workspace.id, user_id=uuid.UUID(user_id))
    turn = await deps.runner.rerender_entity_keys(
        scope, entity_ids=[uuid.UUID(e) for e in entity_ids], timezone=workspace.timezone
    )
    log.info("job.rerender_done", turn_id=str(turn.id), entities=len(entity_ids))
    return {"status": turn.status.value, "turn_id": str(turn.id)}


async def expire_quick(ctx: dict[str, Any]) -> dict[str, int]:
    """Tidy every workspace's quick layer; one system turn per workspace with work to do."""
    deps = _deps(ctx)
    workspaces = await deps.identity.all_workspaces()
    turns = failed = 0
    for workspace in workspaces:
        scope = WorkspaceScope(workspace_id=workspace.id, user_id=workspace.owner_user_id)
        try:
            turn = await deps.runner.expire_quick(scope, timezone=workspace.timezone)
        except Exception:
            failed += 1
            log.exception("job.expire_quick_failed", workspace_id=str(workspace.id))
            continue
        turns += turn is not None
    log.info("job.expire_quick_done", workspaces=len(workspaces), turns=turns, failed=failed)
    return {"workspaces": len(workspaces), "turns": turns, "failed": failed}
