"""ADR-0045: the web tier compresses the static site only.

The template and the page are read as text: nginx itself is exercised by the e2e spec
(`frontend/e2e/delivery.spec.ts`) against the running stack.
"""

import re
from pathlib import Path

WEB = Path(__file__).parents[3] / "frontend"
TEMPLATE = (WEB / "nginx" / "default.conf.template").read_text()


def _blocks(text: str, opener: str) -> dict[str, str]:
    """Each `opener <head> { body }` in the text, by head; braces are matched, not guessed."""
    found: dict[str, str] = {}
    for match in re.finditer(rf"^\s*{opener}\b([^{{;]*)\{{", text, re.MULTILINE):
        depth, end = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        found[match.group(1).strip()] = text[match.end() : end - 1]
    return found


LOCATIONS = _blocks(TEMPLATE, "location")
API = next(head for head in LOCATIONS if "v1/" in head)
GZIP_ON = re.compile(r"^\s*gzip on;", re.MULTILINE)


def _gzip_types() -> list[str]:
    declared = re.search(r"gzip_types\s+([^;]+);", TEMPLATE)
    assert declared, "gzip_types is not set"
    return declared.group(1).split()


def test_the_static_locations_are_compressed() -> None:
    static = [head for head in LOCATIONS if head not in {API, "= /healthz"}]
    assert {"/", "/assets/", "= /design"} <= set(static)
    for head in static:
        assert GZIP_ON.search(LOCATIONS[head]), f"location {head} is not compressed"


def test_the_api_and_its_streams_are_never_compressed() -> None:
    # Responses there carry one person's data (BREACH) and a stream that must not be held back.
    assert "gzip" not in LOCATIONS[API]
    server = next(iter(_blocks(TEMPLATE, "server").values()))
    for body in LOCATIONS.values():
        server = server.replace(body, "")
    assert not GZIP_ON.search(server), "gzip is on for the whole server"
    assert "text/event-stream" not in _gzip_types()
    assert "font/woff2" not in _gzip_types(), "fonts are already compressed"


def test_the_built_script_is_among_the_compressed_types() -> None:
    assert {"text/css", "application/javascript", "application/json"} <= set(_gzip_types())
