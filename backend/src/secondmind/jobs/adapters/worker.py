"""arq worker entrypoint: ``arq secondmind.jobs.adapters.worker.WorkerSettings``.

Config is loaded from the environment and validated at import (start-up); an invalid config
exits with a message naming the variable. ``arq --check`` uses the health key for readiness.
The runtime (database, models, memory, turn runner) is built once when the worker starts, and
imported only then: ``arq --check`` imports this module every 10 s and must stay fast.
"""

import sys
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from secondmind.config import load_app_config
from secondmind.core import ConfigError
from secondmind.jobs import DEPS_KEY, EXPIRE_QUICK_EVERY_MINUTES, JOBS, JobDeps, expire_quick
from secondmind.observability import configure_logging, get_logger

try:
    _config = load_app_config()
except ConfigError as exc:
    sys.stderr.write(f"{exc.message}\n")
    raise SystemExit(2) from exc

_settings = _config.settings
configure_logging(
    level=_settings.log_level,
    fmt=_settings.log_format,
    include_content=_settings.log_include_content,
)
log = get_logger(__name__)

RUNTIME_KEY = "runtime"


async def _startup(ctx: dict[str, Any]) -> None:
    from secondmind.agent.adapters import (  # noqa: PLC0415
        build_runtime,
        require_embedding_dimensions,
    )
    from secondmind.auth.adapters import SqlIdentityStore  # noqa: PLC0415

    runtime = build_runtime(_config)
    await require_embedding_dimensions(runtime.db, _settings.embed_dimensions)
    ctx[RUNTIME_KEY] = runtime
    ctx[DEPS_KEY] = JobDeps(runner=runtime.runner, identity=SqlIdentityStore(runtime.db))
    log.info("worker.started", jobs=[f.__name__ for f in JOBS])


async def _shutdown(ctx: dict[str, Any]) -> None:
    runtime = ctx.pop(RUNTIME_KEY, None)
    if runtime is not None:
        await runtime.aclose()
    log.info("worker.stopped")


class WorkerSettings:
    functions: ClassVar[list[Any]] = list(JOBS)
    cron_jobs: ClassVar[list[Any]] = [
        cron(
            expire_quick,
            name="cron:expire_quick",
            minute=set(range(0, 60, EXPIRE_QUICK_EVERY_MINUTES)),
            unique=True,
        )
    ]
    redis_settings = RedisSettings.from_dsn(str(_settings.redis_url))
    on_startup = _startup
    on_shutdown = _shutdown
    health_check_interval = 10
    job_timeout = 300
    max_tries = 3
