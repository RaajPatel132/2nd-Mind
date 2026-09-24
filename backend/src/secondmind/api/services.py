"""Composition root: the one place that builds adapters and wires them into the domain."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from secondmind.agent import TurnRunner
from secondmind.agent.adapters import (
    build_runtime,
    check_embedding_dimensions,
    require_embedding_dimensions,
)
from secondmind.auth import IdentityStore, SessionSigner
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.config import AppConfig
from secondmind.core import Clock, WorkspaceScope, utc_now
from secondmind.jobs.adapters import QueueClient
from secondmind.memory.adapters import SCHEMA_HEAD
from secondmind.observability import Tracer, get_logger

log = get_logger(__name__)

CHECK_TIMEOUT_S = 3.0


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    detail: str


Check = Callable[[], Awaitable[CheckResult]]


@dataclass
class Services:
    config: AppConfig
    identity: IdentityStore
    runner: TurnRunner
    tracer: Tracer
    signer: SessionSigner
    checks: Mapping[str, Check]
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    clock: Clock = utc_now

    async def run_checks(self) -> dict[str, CheckResult]:
        async def guarded(check: Check) -> CheckResult:
            try:
                return await asyncio.wait_for(check(), CHECK_TIMEOUT_S)
            except TimeoutError:
                return CheckResult(ok=False, detail=f"timed out after {CHECK_TIMEOUT_S:g}s")
            except Exception as exc:
                return CheckResult(ok=False, detail=f"{type(exc).__name__}: {exc}"[:300])

        names = list(self.checks)
        results = await asyncio.gather(*(guarded(self.checks[n]) for n in names))
        return dict(zip(names, results, strict=True))

    async def aclose(self) -> None:
        await self.runner.aclose()
        for close in reversed(self.closers):
            try:
                await close()
            except Exception:
                log.exception("services.close_failed")


def provider_check(config: AppConfig) -> Check:
    async def check() -> CheckResult:
        routing = config.routing
        fake = sorted(
            {r.step.value for r in routing.routes.values() if r.primary.provider == "fake"}
        )
        detail = f"mode={routing.mode}"
        if fake:
            detail += f"; fake provider for: {', '.join(fake)}"
        return CheckResult(ok=True, detail=detail)

    return check


async def build_services(config: AppConfig) -> Services:
    settings = config.settings
    queue = QueueClient(str(settings.redis_url))

    async def rerender(scope: WorkspaceScope, entity_ids: set[uuid.UUID]) -> None:
        await queue.enqueue(
            "rerender_entity_keys",
            str(scope.workspace_id),
            str(scope.user_id),
            sorted(str(e) for e in entity_ids),
        )

    runtime = build_runtime(config, on_entities_renamed=rerender)
    db = runtime.db
    await require_embedding_dimensions(db, settings.embed_dimensions)

    async def database_check() -> CheckResult:
        await db.ping()
        revision = await db.schema_revision()
        if revision != SCHEMA_HEAD:
            return CheckResult(ok=False, detail=f"schema at {revision}, expected {SCHEMA_HEAD}")
        role = await db.role_check()
        if not role.safe:
            return CheckResult(ok=False, detail=f"role {role.role!r} can bypass row-level security")
        problem = await check_embedding_dimensions(db, settings.embed_dimensions)
        if problem:
            return CheckResult(ok=False, detail=problem)
        return CheckResult(ok=True, detail=f"schema {revision}; role {role.role} under RLS")

    async def redis_check() -> CheckResult:
        return CheckResult(ok=await queue.ping(), detail="ping")

    return Services(
        config=config,
        identity=SqlIdentityStore(db),
        runner=runtime.runner,
        tracer=runtime.tracer,
        signer=SessionSigner(settings.session_secret.get_secret_value()),
        checks={
            "database": database_check,
            "redis": redis_check,
            "providers": provider_check(config),
        },
        closers=[runtime.aclose, queue.aclose],
    )
