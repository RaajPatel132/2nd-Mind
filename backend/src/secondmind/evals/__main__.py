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


async def _seed_dev(email: str | None) -> int:
    """Seed the recall fixture into the dev user's workspace (``make seed-dev``)."""
    from secondmind.agent.adapters import build_runtime  # noqa: PLC0415
    from secondmind.auth.adapters import SqlIdentityStore  # noqa: PLC0415
    from secondmind.config import load_app_config  # noqa: PLC0415
    from secondmind.core import WorkspaceScope  # noqa: PLC0415
    from secondmind.evals.recall import seed_into  # noqa: PLC0415

    config = load_app_config()
    runtime = build_runtime(config)
    try:
        user, ws = await SqlIdentityStore(runtime.db).ensure_user_with_private_workspace(
            email=email or config.settings.dev_user_email,
            timezone=config.settings.default_timezone,
        )
        scope = WorkspaceScope(workspace_id=ws.id, user_id=user.id)
        seeded = await seed_into(runtime.db, scope, runtime.router, runtime.memory)
    finally:
        await runtime.aclose()
    sys.stdout.write(
        f"seeded {len(seeded.items)} memories\n" if seeded else "already seeded; nothing to do\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m secondmind.evals")
    sub = parser.add_subparsers(dest="suite", required=True)
    ingest = sub.add_parser("ingest", help="ingestion golden cases (S2.13)")
    ingest.add_argument("--live", action="store_true", help="final turns on real providers")
    ingest.add_argument("--case", action="append", default=[], help="run only this case id")
    seed = sub.add_parser("seed-dev", help="seed the recall fixture into the dev workspace")
    seed.add_argument("--email", default=None, help="the user to seed (default DEV_USER_EMAIL)")
    args = parser.parse_args(argv)
    configure_logging(level="WARNING", fmt="console")  # the table is the output, not turn logs
    try:
        if args.suite == "seed-dev":
            return asyncio.run(_seed_dev(args.email))
        return asyncio.run(_ingest(args.live, args.case))
    except ConfigError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
