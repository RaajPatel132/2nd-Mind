"""OpenAPI snapshot: ``--write openapi.json`` regenerates it, ``--check openapi.json`` fails if
the committed snapshot is out of date with the code (the start of FR-17.6)."""

import argparse
import difflib
import json
import sys
from pathlib import Path

from secondmind.api.app import create_app


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path)
    group.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    current = render()
    if args.write:
        args.write.write_text(current)
        sys.stdout.write(f"wrote {args.write}\n")
        return 0
    committed = args.check.read_text() if args.check.exists() else ""
    if committed == current:
        sys.stdout.write("openapi snapshot is up to date\n")
        return 0
    diff = difflib.unified_diff(
        committed.splitlines(), current.splitlines(), "committed", "generated", lineterm="", n=2
    )
    sys.stderr.write("\n".join(list(diff)[:80]) + "\n")
    sys.stderr.write(
        f"\n{args.check} is out of date with the code. Run `make gen-client` and commit it.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
