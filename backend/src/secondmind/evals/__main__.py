"""``python -m secondmind.evals ingest [--live] [--case ID ...]``: run the ingestion golden cases
and print the per-field score table with p50/p95 latency and cost (S2.13).

Without ``--live`` every call replays the recorded outputs (the same as the unit tests). With
``--live`` each case's final turn goes to the configured real providers; there is no gate yet,
the table is the baseline for the sprint report.
"""

import argparse
import asyncio
import sys

from secondmind.core import ConfigError
from secondmind.evals.ingest import live_router, load_cases, report, run_case, score
from secondmind.observability import configure_logging


async def _ingest(live: bool, only: list[str]) -> int:
    cases = [c for c in load_cases() if not only or c.id in only]
    if not cases:
        sys.stderr.write(f"no cases match {only}\n")
        return 2
    router = live_router() if live else None
    runs = []
    try:
        for case in cases:
            run = await run_case(case, live=router)
            runs.append((run, score(run)))
            sys.stderr.write(f"  {case.id}: {run.latency_ms} ms\n")
    finally:
        if router is not None:
            await router.aclose()
    mode = "live" if live else "replayed"
    sys.stdout.write(f"ingest golden cases ({mode}), {len(runs)} cases\n{report(runs)}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m secondmind.evals")
    sub = parser.add_subparsers(dest="suite", required=True)
    ingest = sub.add_parser("ingest", help="ingestion golden cases (S2.13)")
    ingest.add_argument("--live", action="store_true", help="final turns on real providers")
    ingest.add_argument("--case", action="append", default=[], help="run only this case id")
    args = parser.parse_args(argv)
    configure_logging(level="WARNING", fmt="console")  # the table is the output, not turn logs
    try:
        return asyncio.run(_ingest(args.live, args.case))
    except ConfigError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
