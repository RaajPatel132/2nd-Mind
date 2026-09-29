"""Typed errors shared across modules. Each has a stable machine code and a safe message."""


class SecondMindError(Exception):
    """Base error. ``code`` is stable and machine-readable; ``message`` is safe to show users."""

    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigError(SecondMindError):
    code = "config_error"


class NotFoundError(SecondMindError):
    code = "not_found"


class UnauthenticatedError(SecondMindError):
    code = "unauthenticated"


class ForbiddenError(SecondMindError):
    code = "forbidden"


class ValidationFailedError(SecondMindError):
    code = "validation_failed"


class RateLimitedError(SecondMindError):
    """Too many requests from one identity (ADR-0032): try again after ``retry_after_s``."""

    code = "rate_limited"

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
