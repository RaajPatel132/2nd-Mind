"""Enqueue a one-off job by name: ``python -m secondmind.jobs.adapters.enqueue <job>``.

Used by make targets such as ``make backfill-conversation``; the running worker does the work.
"""

import asyncio
import sys

from secondmind.config import load_settings
from secondmind.jobs import JOBS
from secondmind.jobs.adapters.queue import QueueClient

ONE_OFF = {"backfill_conversation"}


async def _main(name: str) -> int:
    if name not in ONE_OFF or name not in {f.__name__ for f in JOBS}:
        sys.stderr.write(f"unknown one-off job {name!r}; try one of {sorted(ONE_OFF)}\n")
        return 2
    queue = QueueClient(str(load_settings().redis_url))
    try:
        job = await queue.enqueue(name)
    finally:
        await queue.aclose()
    sys.stdout.write(f"enqueued {name} as {job.job_id}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "")))
