"""Process entrypoint: ``uvicorn --factory secondmind.api.main:create``.

Configuration is loaded and validated here, at start-up; an invalid config exits with a
message naming every offending variable (NFR-9.4).
"""

import sys

from fastapi import FastAPI

from secondmind.api.app import create_app
from secondmind.api.services import build_services
from secondmind.config import load_app_config
from secondmind.core import ConfigError
from secondmind.observability import configure_logging


def create() -> FastAPI:
    try:
        config = load_app_config()
    except ConfigError as exc:
        sys.stderr.write(f"{exc.message}\n")
        raise SystemExit(2) from exc
    settings = config.settings
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        include_content=settings.log_include_content,
    )
    return create_app(services_factory=lambda: build_services(config))
