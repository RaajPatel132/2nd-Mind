"""No-op job proving the queue path end to end (enqueue -> worker -> result)."""

from typing import Any

from secondmind.core import utc_now
from secondmind.observability import get_logger

log = get_logger(__name__)


async def heartbeat(ctx: dict[str, Any], note: str = "") -> dict[str, str]:
    at = utc_now().isoformat()
    log.info("job.heartbeat", job_id=ctx.get("job_id"))
    return {"status": "ok", "at": at, "note": note}
