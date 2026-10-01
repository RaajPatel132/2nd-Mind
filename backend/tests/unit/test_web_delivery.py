"""ADR-0045: the web tier compresses the static site only, and fills in its own address safely.

The template and the page are read as text: nginx itself is exercised by the e2e spec
(`frontend/e2e/delivery.spec.ts`) against the running stack.
"""

import re
from pathlib import Path

WEB = Path(__file__).parents[3] / "frontend"
TEMPLATE = (WEB / "nginx" / "default.conf.template").read_text()
INDEX = (WEB / "index.html").read_text()
PLACEHOLDER = "__SITE_ORIGIN__"


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


def test_the_page_names_its_address_only_in_the_preview_tags() -> None:
    page = re.sub(r"<!--.*?-->", "", INDEX, flags=re.DOTALL)  # the comment explaining it names it
    uses = re.findall(rf'<meta[^>]*\scontent="([^"]*{PLACEHOLDER}[^"]*)"', page)
    assert uses, "the preview tags carry no address to fill in"
    assert page.count(PLACEHOLDER) == len(uses), "the placeholder is used outside a content tag"
    assert any(use.endswith("/og.png") for use in uses)


def test_the_web_tier_fills_the_placeholder_from_a_checked_value() -> None:
    assert re.search(rf"sub_filter\s+'{PLACEHOLDER}'\s+'\$site_origin';", TEMPLATE)
    assert "sub_filter_once off;" in TEMPLATE
    origin = _blocks(TEMPLATE, "map")['"$http_x_forwarded_proto|$http_host" $site_origin']
    assert 'default "";' in origin, "an unchecked Host must give an empty origin"
    patterns = re.findall(r'"~([^"]+)"', origin)
    assert len(patterns) == 2
    for pattern in patterns:
        # Hostname characters and a port only: nothing that could close an attribute or a tag.
        assert "[A-Za-z0-9.-]+" in pattern
        assert not re.search(r"\.\*|\\S|\\w|\[\^", pattern), f"too loose: {pattern}"
