"""``python -m secondmind.evals ingest [--live] [--case ID ...]``: run the ingestion golden cases
and print the per-field score table with p50/p95 latency and cost (S2.13).

Without ``--live`` every call replays the recorded outputs (the same as the unit tests). With
``--live`` each case's final turn goes to the configured real providers; there is no gate yet,
the table is the baseline for the sprint report.
"""

import argparse
import asyncio
import os
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


async def _seed_dev(email: str | None, web_url: str) -> int:
    """Reset the dev user's workspace and seed the recall fixture into it (``make seed-dev``)."""
    from secondmind.agent.adapters import build_runtime  # noqa: PLC0415
    from secondmind.auth.adapters import SqlIdentityStore  # noqa: PLC0415
    from secondmind.config import load_app_config  # noqa: PLC0415
    from secondmind.core import WorkspaceScope  # noqa: PLC0415
    from secondmind.evals.recall import seed_into  # noqa: PLC0415
    from secondmind.memory.adapters import reset_workspace  # noqa: PLC0415

    config = load_app_config()
    if not config.settings.dev_auth:
        sys.stderr.write("seed-dev: DEV_AUTH is off; refusing to reset a workspace\n")
        return 2
    owner_url = os.environ.get("DATABASE_MIGRATION_URL", "")
    if not owner_url:
        sys.stderr.write("seed-dev: DATABASE_MIGRATION_URL is needed to reset the workspace\n")
        return 2
    login = email or config.settings.dev_user_email
    runtime = build_runtime(config)
    try:
        user, ws = await SqlIdentityStore(runtime.db).ensure_user_with_private_workspace(
            email=login, timezone=config.settings.default_timezone
        )
        scope = WorkspaceScope(workspace_id=ws.id, user_id=user.id)
        await reset_workspace(owner_url, scope)
        seeded = await seed_into(
            runtime.db,
            scope,
            runtime.router,
            runtime.memory,
            resources=config.settings.resources_dir,
        )
    finally:
        await runtime.aclose()
    count = len(seeded.items) if seeded else 0
    sys.stdout.write(
        f"Reset the workspace of {login} and seeded {count} memories (the recall fixture).\n"
        f"Sign in: open {web_url}; the dev login signs you in as {login}.\n"
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
    seed.add_argument("--web-url", default="http://localhost:8080", help="printed with the login")
    args = parser.parse_args(argv)
    configure_logging(level="WARNING", fmt="console")  # the table is the output, not turn logs
    try:
        if args.suite == "seed-dev":
            return asyncio.run(_seed_dev(args.email, args.web_url))
        return asyncio.run(_ingest(args.live, args.case))
    except ConfigError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
