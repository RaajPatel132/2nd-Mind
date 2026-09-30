"""S4.6: the safe fetcher is the only way the app requests a URL a person gave. The import
contract covers the third-party HTTP libraries; this covers the standard library's own clients,
and that nothing outside links.adapters builds a request."""

import re
from pathlib import Path

SRC = Path(__file__).parents[3] / "src" / "secondmind"
DOOR = SRC / "links" / "adapters"
# observability's exporters talk to Langfuse (a fixed, configured host), not to a person's URLs.
ELSEWHERE_OK = {SRC / "observability"}

CLIENTS = re.compile(
    r"\b(?:import|from)\s+(?:httpx|requests|aiohttp|urllib3|http\.client|urllib\.request|ftplib|"
    r"smtplib|telnetlib|xmlrpc)\b|\bsocket\.(?:create_connection|socket)\b|\burlopen\("
)


def test_nothing_outside_the_safe_fetcher_makes_an_http_request() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        if DOOR in path.parents or any(ok in path.parents for ok in ELSEWHERE_OK):
            continue
        if CLIENTS.search(path.read_text()):
            offenders.append(path.relative_to(SRC).as_posix())
    assert not offenders, f"these reach for an HTTP client: {offenders}"


def test_only_the_fetcher_module_uses_the_http_client_inside_the_door() -> None:
    users = [
        p.name
        for p in DOOR.rglob("*.py")
        if re.search(r"\bimport httpx\b|\bfrom httpx\b", p.read_text())
    ]
    assert users, "the fetcher should use httpx"
    assert set(users) <= {"fetcher.py", "video.py"}, users
