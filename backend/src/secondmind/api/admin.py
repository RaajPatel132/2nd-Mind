"""The admin CLI (ADR-0032): what an operator does without a deploy.

    python -m secondmind.api.admin kill-switch on|off|status
    python -m secondmind.api.admin set-tier EMAIL guest|standard|premium
    python -m secondmind.api.admin spend

``kill-switch`` flips the flag every api and worker process reads (within seconds, no restart).
``set-tier`` changes a person's tier and writes the audit row (who, when, from, to); it connects
as the schema owner (``DATABASE_MIGRATION_URL``), because the app's own role can't write the
audit. ``spend`` prints today's, this month's and each provider's counters. Nothing here prints a
key or a person's content.
"""

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Sequence

from secondmind.auth.adapters import SqlAdminStore
from secondmind.config import load_app_config
from secondmind.core import ConfigError, NotFoundError, Tier, utc_now
from secondmind.memory.adapters import Database
from secondmind.metering.adapters import RedisSpendStore, spend_limits


def _say(text: str = "", *, error: bool = False) -> None:
    (sys.stderr if error else sys.stdout).write(text + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secondmind.api.admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    kill = sub.add_parser("kill-switch", help="stop or resume every model call")
    kill.add_argument("state", choices=("on", "off", "status"))
    tier = sub.add_parser("set-tier", help="change a person's tier (audited)")
    tier.add_argument("email")
    tier.add_argument("tier", choices=[t.value for t in Tier])
    sub.add_parser("spend", help="print the spend counters")
    return parser


async def kill_switch(state: str) -> int:
    config = load_app_config()
    store = RedisSpendStore.from_url(str(config.settings.redis_url))
    try:
        if state in ("on", "off"):
            await store.set_kill_switch(state == "on")
        flag = await store.kill_switch()
    finally:
        await store.aclose()
    env = config.settings.kill_switch
    effective = env if flag is None else flag
    source = "the runtime flag" if flag is not None else "KILL_SWITCH in the environment"
    _say(f"kill switch is {'ON' if effective else 'off'} (from {source})")
    if state == "on":
        _say("Every api and worker process refuses new model work within a few seconds.")
    return 0


async def set_tier(email: str, tier: str) -> int:
    url = os.environ.get("DATABASE_MIGRATION_URL", "")
    if not url:
        _say("DATABASE_MIGRATION_URL is required: set-tier writes the audit as the owner.")
        return 2
    db = Database(url, pool_size=1)
    try:
        change = await SqlAdminStore(db).set_tier(
            email=email, tier=Tier(tier), changed_by=f"{getpass.getuser()} (admin CLI)"
        )
    except NotFoundError as exc:
        _say(exc.message)
        return 1
    finally:
        await db.dispose()
    _say(
        f"{email}: {change.from_tier.value} -> {change.to_tier.value} "
        f"(by {change.changed_by}, at {change.changed_at.isoformat(timespec='seconds')})"
    )
    _say("Their quota and model picker follow it on their next turn.")
    return 0


async def spend() -> int:
    config = load_app_config()
    limits = spend_limits(config)
    store = RedisSpendStore.from_url(str(config.settings.redis_url))
    try:
        totals = await store.totals(utc_now())
    finally:
        await store.aclose()
    _say(f"today   ${totals.day:.4f} of ${limits.daily_usd:.2f}")
    _say(f"month   ${totals.month:.4f} of ${limits.monthly_usd:.2f}")
    for provider, spent in sorted(totals.providers.items()):
        credit = limits.credits_usd.get(provider)
        _say(f"{provider:<8}${spent:.4f}" + (f" of ${credit:.2f} credit" if credit else ""))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "kill-switch":
            return asyncio.run(kill_switch(args.state))
        if args.command == "set-tier":
            return asyncio.run(set_tier(args.email, args.tier))
        return asyncio.run(spend())
    except ConfigError as exc:
        _say(exc.message, error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
