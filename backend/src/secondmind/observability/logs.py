"""Structured JSON logs that carry request_id and turn_id (NFR-5.4) and no message content.

Content is redacted by key unless ``LOG_INCLUDE_CONTENT`` is on (refused in production).
Code should never log content in the first place; redaction is the backstop.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, unbind_contextvars
from structlog.typing import EventDict, WrappedLogger

CONTENT_KEYS = frozenset(
    {"content", "text", "message_text", "input", "output", "prompt", "completion", "reply"}
)


def redact_content(include_content: bool) -> structlog.typing.Processor:
    def processor(_logger: WrappedLogger, _name: str, event: EventDict) -> EventDict:
        if include_content:
            return event
        for key in CONTENT_KEYS & event.keys():
            value = event[key]
            size = len(value) if isinstance(value, str) else None
            event[key] = f"<redacted {size} chars>" if size is not None else "<redacted>"
        return event

    return processor


def configure_logging(
    *, level: str = "INFO", fmt: str = "json", include_content: bool = False
) -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_content(include_content),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "arq"):
        lib = logging.getLogger(name)
        lib.handlers = []
        lib.propagate = True
    # Third-party request logs can carry URLs with content; keep them at WARNING.
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "anthropic", "langfuse"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


def bind_log_context(**values: Any) -> None:
    bind_contextvars(**values)


def unbind_log_context(*keys: str) -> None:
    unbind_contextvars(*keys)


def clear_log_context() -> None:
    clear_contextvars()


def current_log_context() -> MutableMapping[str, Any]:
    return structlog.contextvars.get_contextvars()
