"""Guest expiry (S4.12, ADR-0039): a daily job empties the memory of guests older than
``GUEST_TTL_DAYS``. Every table with content is emptied; the usage ledger (costs only) is kept, so
the caps and the totals stay right."""

from datetime import timedelta
from typing import Any

from secondmind.core import utc_now
from secondmind.jobs.memory import _deps
from secondmind.observability import get_logger

log = get_logger(__name__)

# Once a day, in the quiet hours (UTC).
EXPIRE_GUESTS_AT_HOUR = 3
EXPIRE_GUESTS_AT_MINUTE = 15


async def expire_guests(ctx: dict[str, Any]) -> dict[str, int]:
    """Empty the workspaces of guests created more than ``GUEST_TTL_DAYS`` ago."""
    deps = _deps(ctx)
    before = utc_now() - timedelta(days=deps.guest_ttl_days)
    emptied = await deps.identity.expire_guests(before)
    log.info("job.expire_guests_done", workspaces=emptied, ttl_days=deps.guest_ttl_days)
    return {"workspaces": emptied}
