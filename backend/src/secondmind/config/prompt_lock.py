"""CLI: append new prompt versions to prompts.lock.json (never rewrites released ones)."""

import sys

from secondmind.config.prompts import update_lock
from secondmind.config.settings import DEFAULT_RESOURCES_DIR
from secondmind.core import ConfigError


def main() -> int:
    try:
        added = update_lock(DEFAULT_RESOURCES_DIR / "prompts")
    except ConfigError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1
    for ref in added:
        sys.stdout.write(f"locked {ref}\n")
    if not added:
        sys.stdout.write("prompt lock is up to date\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
