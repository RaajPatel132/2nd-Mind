"""Wall-clock access in one place, so code can take "now" explicitly and tests can freeze it."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)
