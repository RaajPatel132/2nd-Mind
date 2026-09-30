"""Memory housekeeping jobs. Each change runs as a system turn through the turn runner, so it's
auditable and undoable like anything a user does (S2.9, S2.14)."""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from secondmind.core import WorkspaceScope
from secondmind.observability import get_logger
from secondmind.providers import CallsRefusedError

if TYPE_CHECKING:  # the worker's health check imports this module; keep it light
    from secondmind.agent import TurnRunner
    from secondmind.auth import IdentityStore
    from secondmind.metering import SpendGate, SpendReader

log = get_logger(__name__)

DEPS_KEY = "deps"
# How often the quick layer is tidied (the worker's cron schedule).
EXPIRE_QUICK_EVERY_MINUTES = 10
# How long a job that needs a model waits when the spend gate stops it (ADR-0032).
DEFER_SECONDS = 300


class JobDeferred(Exception):  # noqa: N818 - a signal to the worker, not a failure
    """The job can't run yet (the kill switch is on, or a spend cap is reached): the worker
    puts it back on the queue for later. It is deferred, never dropped."""

    def __init__(self, reason: str, seconds: int = DEFER_SECONDS) -> None:
        super().__init__(reason)
        self.reason = reason
        self.seconds = seconds


@dataclass(frozen=True, slots=True)
class JobDeps:
    """What memory jobs need, put into the arq context by the worker at start-up."""

    runner: "TurnRunner"
    identity: "IdentityStore"
    gate: "SpendGate | None" = None
    spend: "SpendReader | None" = None


def _deps(ctx: dict[str, Any]) -> JobDeps:
    deps = ctx.get(DEPS_KEY)
    if not isinstance(deps, JobDeps):
        raise RuntimeError("the worker started without its runtime")
    return deps


async def _may_use_models(deps: JobDeps) -> None:
    """Defer a job that needs a model while the spend gate is stopping new model work."""
    if deps.gate is None:
        return
    reason = await deps.gate.refuse()
    if reason is not None:
        raise JobDeferred(reason)


async def rerender_entity_keys(
    ctx: dict[str, Any], workspace_id: str, user_id: str, entity_ids: list[str]
) -> dict[str, str]:
    """Re-render the keys of items linked to renamed or relabelled entities (S2.14)."""
    deps = _deps(ctx)
    await _may_use_models(deps)
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


async def index_conversation(
    ctx: dict[str, Any], workspace_id: str, user_id: str, turn_id: str
) -> dict[str, int]:
    """Index what was said in one completed chat turn (S3.9), after the reply has gone."""
    deps = _deps(ctx)
    await _may_use_models(deps)
    scope = WorkspaceScope(workspace_id=uuid.UUID(workspace_id), user_id=uuid.UUID(user_id))
    try:
        rows = await deps.runner.index_conversation(scope, uuid.UUID(turn_id))
    except CallsRefusedError as exc:  # the switch flipped while it ran
        raise JobDeferred(exc.reason) from exc
    return {"rows": rows}


async def backfill_conversation(ctx: dict[str, Any]) -> dict[str, int]:
    """Index every past chat turn not indexed yet, in every workspace (the one-off job)."""
    deps = _deps(ctx)
    await _may_use_models(deps)
    workspaces = await deps.identity.all_workspaces()
    rows = 0
    for workspace in workspaces:
        scope = WorkspaceScope(workspace_id=workspace.id, user_id=workspace.owner_user_id)
        try:
            rows += await deps.runner.backfill_conversation(scope)
        except CallsRefusedError as exc:
            raise JobDeferred(exc.reason) from exc
    log.info("job.backfill_conversation_done", workspaces=len(workspaces), rows=rows)
    return {"workspaces": len(workspaces), "rows": rows}


async def fetch_link(
    ctx: dict[str, Any], workspace_id: str, user_id: str, item_id: str
) -> dict[str, str]:
    """Read a saved link's page (S4.7): fetch it safely, digest, chunk and embed it, and write the
    result onto the turn that saved the link. Checks the spend gate like any job that needs a
    model; its cost is the person's, on that turn's ledger."""
    deps = _deps(ctx)
    await _may_use_models(deps)
    workspace = await deps.identity.get_workspace(uuid.UUID(workspace_id))
    if workspace is None:
        log.warning("job.fetch_link_skipped", reason="workspace gone")
        return {"status": "skipped"}
    scope = WorkspaceScope(workspace_id=workspace.id, user_id=uuid.UUID(user_id))
    try:
        result = await deps.runner.read_link(scope, uuid.UUID(item_id), timezone=workspace.timezone)
    except CallsRefusedError as exc:  # the switch flipped, or a cap was reached, while it ran
        raise JobDeferred(exc.reason) from exc
    if result is None:
        return {"status": "skipped"}
    log.info("job.fetch_link_done", status=result.status.value, rule=result.rule)
    return {"status": result.status.value}
