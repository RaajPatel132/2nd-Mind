"""arq worker entrypoint: ``arq secondmind.jobs.adapters.worker.WorkerSettings``.

Config is loaded from the environment and validated at import (start-up); an invalid config
exits with a message naming the variable. ``arq --check`` uses the health key for readiness.
"""

import sys
from typing import Any, ClassVar

from arq.connections import RedisSettings

from secondmind.config import load_settings
from secondmind.core import ConfigError
from secondmind.jobs import JOBS
from secondmind.observability import configure_logging, get_logger

try:
    _settings = load_settings()
except ConfigError as exc:
    sys.stderr.write(f"{exc.message}\n")
    raise SystemExit(2) from exc

configure_logging(
    level=_settings.log_level,
    fmt=_settings.log_format,
    include_content=_settings.log_include_content,
)
log = get_logger(__name__)


async def _startup(ctx: dict[str, Any]) -> None:
    log.info("worker.started", jobs=[f.__name__ for f in JOBS])


async def _shutdown(ctx: dict[str, Any]) -> None:
    log.info("worker.stopped")


class WorkerSettings:
    functions: ClassVar[list[Any]] = list(JOBS)
    redis_settings = RedisSettings.from_dsn(str(_settings.redis_url))
    on_startup = _startup
    on_shutdown = _shutdown
    health_check_interval = 10
    job_timeout = 300
    max_tries = 3
