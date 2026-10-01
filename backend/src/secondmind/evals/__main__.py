"""``python -m secondmind.evals``: run a suite, report runs, check live models, seed dev.

Suites (``intent``, ``ingest``, ``recall``) run on the fake provider by default, replaying the
outputs recorded in each case. ``--dry-run`` runs the same way and prints what a live run would
cost; ``--live`` runs on real providers within the budgets (see :mod:`secondmind.evals.harness`).
Every run writes a stamped run file; ``report`` prints one run, or two side by side.

Examples::

    python -m secondmind.evals recall --dry-run --routing economy-b --cases @probe
    python -m secondmind.evals ingest --live --routing economy-b --only failed --from 2026…
    python -m secondmind.evals report <run-a> <run-b>
    python -m secondmind.evals record-replays ingest --from 2026… --diff   # then --write
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from secondmind.core import ConfigError
from secondmind.evals import replays
from secondmind.evals.harness import CONFIGURED, Harness
from secondmind.evals.ingest import CASES_DIR as INGEST_CASES
from secondmind.evals.recall import CASES_DIR as RECALL_CASES
from secondmind.evals.runs import (
    RunRecord,
    failed_ids,
    latest_runs,
    load_run,
    render,
    render_pair,
)
from secondmind.evals.select import Selection, SelectionError, parse_tokens, select
from secondmind.evals.spend import (
    BudgetRefusedError,
    Budgets,
    SpendBook,
)
from secondmind.observability import configure_logging

SUITES = ("intent", "ingest", "recall")


def _selection(args: argparse.Namespace) -> Selection:
    only: tuple[str, ...] | None = None
    if args.only is not None:
        if args.only != "failed" or not args.from_run:
            raise SelectionError("use --only failed --from <run id or file>")
        only = tuple(failed_ids(load_run(args.from_run)))
    return Selection(
        cases=parse_tokens([*args.cases, *getattr(args, "case", [])]),
        sample=args.sample,
        stratify=args.stratify,
        only_failed=only,
    )


def _mode(args: argparse.Namespace) -> Any:
    if args.live:
        return "live"
    return "dry-run" if args.dry_run else "fake"


async def _intent(harness: Harness) -> RunRecord:
    from secondmind.evals.intent import load_cases, run_intent_case  # noqa: PLC0415

    cases = select(
        load_cases(),
        harness.selection,
        suite="intent",
        id_of=lambda c: c.id,
        tags_of=lambda c: c.tags,
        stratum_of=lambda c: c.intent,
    )
    return await harness.run(cases, run_intent_case, id_of=lambda c: c.id)


async def _ingest(harness: Harness) -> RunRecord:
    from secondmind.evals.ingest import case_tags, load_cases, run_ingest_case  # noqa: PLC0415

    cases = select(
        load_cases(),
        harness.selection,
        suite="ingest",
        id_of=lambda c: c.id,
        tags_of=case_tags,
        stratum_of=lambda c: (case_tags(c) or ["plain"])[0],
    )
    return await harness.run(cases, run_ingest_case, id_of=lambda c: c.id)


def _recall(harness: Harness) -> RunRecord:
    from secondmind.auth.adapters import SqlIdentityStore  # noqa: PLC0415
    from secondmind.evals.adapters import throwaway_postgres  # noqa: PLC0415
    from secondmind.evals.recall import (  # noqa: PLC0415
        case_tags,
        load_cases,
        recall_setup,
        run_recall_case,
    )
    from secondmind.memory.adapters import Database  # noqa: PLC0415

    cases = select(
        load_cases(),
        harness.selection,
        suite="recall",
        id_of=lambda c: c.id,
        tags_of=case_tags,
        stratum_of=lambda c: c.primary_shape,
    )
    if not cases:
        raise BudgetRefusedError("no cases selected")
    harness.say("starting a throwaway Postgres…")
    with throwaway_postgres() as url:

        async def go() -> RunRecord:
            db = Database(url, pool_size=5)
            identity = SqlIdentityStore(db)
            try:
                return await harness.run(
                    cases,
                    lambda case, run: run_recall_case(case, run, db=db, identity=identity),
                    id_of=lambda c: c.id,
                    setup=lambda run: recall_setup(run, db=db, identity=identity),
                )
            finally:
                await db.dispose()

        return asyncio.run(go())


def _run_suite(args: argparse.Namespace) -> int:
    fixture: dict[str, str] = {}
    if args.suite == "recall":
        from secondmind.evals.fixture import load_fixture  # noqa: PLC0415

        fx = load_fixture()
        fixture = {"now": str(fx.now), "timezone": fx.timezone}
    elif args.suite == "intent":
        from secondmind.evals.intent import NOW, TIMEZONE  # noqa: PLC0415

        fixture = {"now": NOW, "timezone": TIMEZONE}
    harness = Harness(
        suite=args.suite,
        mode=_mode(args),
        routing=args.routing,
        selection=_selection(args),
        cache=not args.no_cache,
        budgets=Budgets.from_env(),
        fixture=fixture,
        note=args.note,
        out=sys.stderr,
    )
    if args.suite == "recall":
        record = _recall(harness)
    elif args.suite == "intent":
        record = asyncio.run(_intent(harness))
    else:
        record = asyncio.run(_ingest(harness))
    sys.stdout.write(render(record) + "\n")
    if record.status == "stopped: error":
        return 1
    return 1 if record.mode == "fake" and any(not c.passed for c in record.cases) else 0


def _report(refs: Sequence[str]) -> int:
    if not refs:
        paths = latest_runs()
        if not paths:
            sys.stderr.write("no live runs yet; name a run file or id\n")
            return 2
        sys.stdout.write("\n\n".join(render(load_run(p)) for p in paths) + "\n")
        return 0
    runs = [load_run(r) for r in refs]
    if len(runs) == 1:
        sys.stdout.write(render(runs[0]) + "\n")
    else:
        sys.stdout.write(render_pair(runs[0], runs[1]) + "\n")
    return 0


def _record_replays(args: argparse.Namespace) -> int:
    """Show what a live run's recorded outputs would change in the golden cases' ``model:``
    blocks, and with ``--write`` change them (R.5). The expectations are never touched."""
    run = load_run(args.run)
    if run.suite != args.of:
        sys.stderr.write(f"{run.run_id} is a {run.suite} run, not {args.of}\n")
        return 2
    only = [c.strip() for c in args.cases.split(",")] if args.cases else None
    if only:
        only = [c.id for c in run.cases if any(c.id.startswith(o) for o in only)]
    directory = INGEST_CASES if args.of == "ingest" else RECALL_CASES
    changes = replays.compare(run, args.of, directory, only)
    sys.stdout.write(replays.summary(changes, show_diff=args.diff) + "\n")
    if args.write:
        changed = [c for c in changes if c.changed]
        for change in changed:
            replays.rewrite_model(change.path, change.model)
        sys.stdout.write(f"rewrote the model block of {len(changed)} case files\n")
    return 0


def _spend(clear_halt: bool, record: str | None, note: str | None) -> int:
    book = SpendBook.load()
    if clear_halt:
        book.clear_halt()
    if record:
        # Spend outside the harness (the app stack on real keys): read it off the usage ledger.
        amounts = {
            provider.strip(): Decimal(amount)
            for provider, _, amount in (part.partition("=") for part in record.split(","))
        }
        budgets = Budgets.from_env()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        book.record(
            run_id=f"stack-{stamp}",
            suite="stack",
            spent_by_provider=amounts,
            budget=Decimal(0),
            batch=budgets.batch,
            note=note,
        )
    sys.stdout.write(book.summary(Budgets.from_env().total) + "\n")
    for run in book.runs[-10:]:
        sys.stdout.write(
            f"  {run['at']} {run['suite']:<10} {run.get('batch') or '-':<4} "
            f"${float(run['spent_usd']):.4f}  {run['run_id']}\n"
        )
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
    if not config.settings.dev_helpers:
        sys.stderr.write("seed-dev: needs DEV_AUTH in development; refusing to reset a workspace\n")
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


def _suite_parser(sub: Any, name: str, help_text: str) -> None:
    p = sub.add_parser(name, help=help_text)
    how = p.add_mutually_exclusive_group()
    how.add_argument("--live", action="store_true", help="real providers (costs money)")
    how.add_argument("--dry-run", action="store_true", help="fake provider, estimated live cost")
    p.add_argument("--routing", default=CONFIGURED, help="a candidate in evals/routings/")
    p.add_argument("--cases", action="append", default=[], help="ids, tags or @subsets")
    if name == "ingest":
        p.add_argument("--case", action="append", default=[], help=argparse.SUPPRESS)
    p.add_argument("--sample", type=int, default=None, help="a deterministic sample of N cases")
    p.add_argument("--stratify", default=None, help="cover every stratum first (e.g. shape)")
    p.add_argument("--only", default=None, help="'failed', with --from")
    p.add_argument("--from", dest="from_run", default=None, help="an earlier run (id or file)")
    p.add_argument("--no-cache", action="store_true", help="force fresh calls")
    p.add_argument("--note", default=None, help="a line stored with the run")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m secondmind.evals")
    sub = parser.add_subparsers(dest="suite", required=True)
    _suite_parser(sub, "intent", "intent accuracy on the labelled messages")
    _suite_parser(sub, "ingest", "ingestion golden cases (S2.13)")
    _suite_parser(sub, "recall", "recall golden cases (S3.15), on a throwaway Postgres")
    report = sub.add_parser("report", help="print a run, or two side by side")
    report.add_argument("runs", nargs="*", help="run ids or files (none: latest live per suite)")
    spend = sub.add_parser("spend", help="the live spend so far")
    spend.add_argument("--clear-halt", action="store_true", help="clear a stop-rule halt")
    spend.add_argument("--record", default=None, help="add spend made outside the harness: p=usd,…")
    spend.add_argument("--note", default=None, help="why (stored with the entry)")
    sub.add_parser("live-check", help="one tiny request per routed and picker model")
    record = sub.add_parser(
        "record-replays", help="rebuild the golden cases' model blocks from a live run (R.5)"
    )
    record.add_argument("of", choices=("ingest", "recall"), help="the suite")
    record.add_argument("--from", dest="run", required=True, help="the live run id or file")
    record.add_argument("--cases", default=None, help="ids or prefixes, comma separated")
    record.add_argument("--diff", action="store_true", help="print each case's diff")
    record.add_argument("--write", action="store_true", help="rewrite the case files")
    head = sub.add_parser("headline", help="the landing page's numbers, from the committed runs")
    mode = head.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write frontend/public/headline.json")
    mode.add_argument("--check", action="store_true", help="fail when that file is out of date")
    seed = sub.add_parser("seed-dev", help="seed the recall fixture into the dev workspace")
    seed.add_argument("--email", default=None, help="the user to seed (default DEV_USER_EMAIL)")
    seed.add_argument("--web-url", default="http://localhost:8080", help="printed with the login")
    args = parser.parse_args(argv)
    configure_logging(level="WARNING", fmt="console")  # the table is the output, not turn logs
    commands: dict[str, Callable[[], int]] = {
        "seed-dev": lambda: asyncio.run(_seed_dev(args.email, args.web_url)),
        "report": lambda: _report(args.runs),
        "spend": lambda: _spend(args.clear_halt, args.record, args.note),
        "live-check": _live_check,
        "record-replays": lambda: _record_replays(args),
        "headline": lambda: _headline(args.check),
    }
    try:
        return commands.get(args.suite, lambda: _run_suite(args))()
    except (BudgetRefusedError, SelectionError) as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 3
    except ConfigError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 2
    except FileNotFoundError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


def _headline(check_only: bool) -> int:
    from secondmind.evals import headline  # noqa: PLC0415

    if check_only:
        if headline.check():
            sys.stdout.write("headline.json is up to date\n")
            return 0
        sys.stderr.write("headline.json is out of date: run `make gen-client`\n")
        return 1
    path = headline.write()
    sys.stdout.write(f"wrote {path}\n")
    return 0


def _live_check() -> int:
    from secondmind.evals.livecheck import live_check  # noqa: PLC0415

    return asyncio.run(live_check())


if __name__ == "__main__":
    raise SystemExit(main())
