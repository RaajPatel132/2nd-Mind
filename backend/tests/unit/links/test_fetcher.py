"""S4.6: the safe fetcher's rules, with no network: a fake transport records every request, and a
fake resolver decides what names mean. The real-server versions (a private address, a redirect to
one, a slow response, a gzip bomb) are in tests/integration/test_link_fetch_real_servers.py."""

import asyncio
import gzip
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from secondmind.links import FetchRefusedError
from secondmind.links.adapters import FetchFailedError, FetchPolicy, SafeFetcher

PUBLIC = "93.184.216.34"
HTML = {"content-type": "text/html; charset=utf-8"}


def resolver(mapping: dict[str, list[str]], calls: list[str] | None = None):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        if calls is not None:
            calls.append(host)
        if host not in mapping:
            raise OSError("no such host")
        return mapping[host]

    return resolve


class Server:
    """A fake transport that records the requests it is asked to make."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler or (
            lambda r: httpx.Response(200, headers=HTML, content=b"<p>hi</p>")
        )

    def transport(self) -> httpx.AsyncBaseTransport:
        async def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._handler(request)

        return httpx.MockTransport(handle)


def fetcher(server: Server, mapping: dict[str, list[str]], **policy: Any) -> SafeFetcher:
    return SafeFetcher(
        FetchPolicy(**policy), resolver=resolver(mapping), transport=server.transport()
    )


# ------------------------------------------------------------------ refused before any request


@pytest.mark.parametrize(
    ("addresses", "rule"),
    [
        (["10.0.0.5"], "private_address"),
        (["127.0.0.1"], "loopback"),
        (["169.254.169.254"], "link_local"),
        (["100.64.0.9"], "carrier_grade_nat"),
        (["::1"], "loopback"),
        (["::ffff:10.0.0.1"], "private_address"),
        ([PUBLIC, "10.0.0.5"], "private_address"),  # one bad address refuses the name
    ],
)
async def test_a_name_that_resolves_to_a_private_address_is_refused_before_any_request(
    addresses: list[str], rule: str
) -> None:
    server = Server()
    with pytest.raises(FetchRefusedError) as caught:
        await fetcher(server, {"evil.test": addresses}).fetch("https://evil.test/page")
    assert caught.value.rule == rule
    assert server.requests == []


async def test_a_private_address_in_the_url_is_refused_before_any_request() -> None:
    server = Server()
    for url in ("http://10.0.0.5/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"):
        with pytest.raises(FetchRefusedError):
            await fetcher(server, {}).fetch(url)
    assert server.requests == []


async def test_a_name_that_does_not_resolve_is_a_failure_not_a_refusal() -> None:
    with pytest.raises(FetchFailedError) as caught:
        await fetcher(Server(), {}).fetch("https://nowhere.test/")
    assert caught.value.reason == "dns"


# ------------------------------------------------------------------ one resolution, one connection


async def test_the_host_is_resolved_once_and_the_connection_goes_to_the_checked_address() -> None:
    server, calls = Server(), []
    f = SafeFetcher(
        FetchPolicy(),
        resolver=resolver({"example.test": [PUBLIC, "2606:2800:220:1::1"]}, calls),
        transport=server.transport(),
    )
    page = await f.fetch("https://example.test/a?b=1")
    assert calls == ["example.test"]  # resolved once for the one hop
    (request,) = server.requests
    assert request.url.host == PUBLIC  # the connection target is the address that was checked
    assert request.headers["host"] == "example.test"  # the name is kept for the site and TLS
    assert request.extensions["sni_hostname"] == "example.test"
    assert request.url.path == "/a"
    assert request.url.query == b"b=1"
    assert page.final_host == "example.test"
    assert page.body == b"<p>hi</p>"


async def test_dns_rebinding_cannot_swap_the_address_between_the_check_and_the_connect() -> None:
    answers = iter([[PUBLIC], ["127.0.0.1"], ["127.0.0.1"]])  # public the first time, then private
    calls: list[str] = []

    async def rebinding(host: str, port: int) -> list[str]:
        calls.append(host)
        return next(answers)

    server = Server()
    f = SafeFetcher(FetchPolicy(), resolver=rebinding, transport=server.transport())
    await f.fetch("http://rebind.test/")
    assert len(calls) == 1  # never asked again, so a second answer can't be used
    assert [r.url.host for r in server.requests] == [PUBLIC]


async def test_an_ipv6_address_is_connected_to_in_brackets() -> None:
    server = Server()
    await fetcher(server, {"v6.test": ["2606:2800:220:1::1"]}).fetch("http://v6.test/")
    assert server.requests[0].url.host == "2606:2800:220:1::1"
    assert server.requests[0].headers["host"] == "v6.test"


# ------------------------------------------------------------------ redirects


async def test_each_redirect_is_checked_as_a_new_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "a.test":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        return httpx.Response(200, headers=HTML, content=b"secret")

    server = Server(handler)
    with pytest.raises(FetchRefusedError) as caught:
        await fetcher(server, {"a.test": [PUBLIC]}).fetch("http://a.test/")
    assert caught.value.rule == "link_local"
    assert [r.headers["host"] for r in server.requests] == [
        "a.test"
    ]  # the metadata host: no request


async def test_a_redirect_to_a_private_name_or_a_bad_scheme_or_port_is_refused() -> None:
    def to(location: str) -> Callable[[httpx.Request], httpx.Response]:
        return lambda r: httpx.Response(301, headers={"location": location})

    for location, rule in (
        ("http://intranet.test/admin", "private_address"),
        ("ftp://files.example/x", "scheme"),
        ("http://localhost/", "loopback"),
        ("http://example.test:8080/", "port"),
        ("https://user:pw@example.test/", "credentials"),
    ):
        server = Server(to(location))
        mapping = {"a.test": [PUBLIC], "intranet.test": ["10.1.2.3"], "example.test": [PUBLIC]}
        with pytest.raises(FetchRefusedError) as caught:
            await fetcher(server, mapping).fetch("http://a.test/")
        assert caught.value.rule == rule, location


async def test_redirects_are_followed_up_to_the_limit_and_no_further() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.strip("/") or 0)
        if n < 3:
            return httpx.Response(302, headers={"location": f"/{n + 1}"})
        return httpx.Response(200, headers=HTML, content=b"end")

    server = Server(handler)
    page = await fetcher(server, {"a.test": [PUBLIC]}, max_redirects=5).fetch("http://a.test/")
    assert page.redirects == 3
    assert page.final_url == "http://a.test/3"
    assert page.hosts == ("a.test",) * 4

    endless = Server(lambda r: httpx.Response(302, headers={"location": "/again"}))
    with pytest.raises(FetchRefusedError) as caught:
        await fetcher(endless, {"a.test": [PUBLIC]}, max_redirects=5).fetch("http://a.test/")
    assert caught.value.rule == "too_many_redirects"
    assert len(endless.requests) == 6


# ------------------------------------------------------------------ what comes back


async def test_the_body_is_cut_off_past_the_size_limit_after_decompression() -> None:
    bomb = gzip.compress(b"\x00" * 20_000_000)  # about 20 KB on the wire, 20 MB once decompressed
    assert len(bomb) < 100_000
    server = Server(
        lambda r: httpx.Response(200, headers={**HTML, "content-encoding": "gzip"}, content=bomb)
    )
    with pytest.raises(FetchRefusedError) as caught:
        await fetcher(server, {"a.test": [PUBLIC]}, max_bytes=500_000).fetch("http://a.test/")
    assert caught.value.rule == "too_large"


@pytest.mark.parametrize(
    "content_type", ["application/pdf", "image/png", "application/zip", "video/mp4"]
)
async def test_only_html_xhtml_and_plain_text_are_accepted(content_type: str) -> None:
    server = Server(
        lambda r: httpx.Response(200, headers={"content-type": content_type}, content=b"x")
    )
    with pytest.raises(FetchRefusedError) as caught:
        await fetcher(server, {"a.test": [PUBLIC]}).fetch("http://a.test/file")
    assert caught.value.rule == "content_type"
    assert caught.value.detail == content_type


@pytest.mark.parametrize(
    "content_type", ["text/html", "application/xhtml+xml", "text/plain; charset=iso-8859-1"]
)
async def test_the_three_accepted_types_come_back_with_their_charset(content_type: str) -> None:
    server = Server(
        lambda r: httpx.Response(200, headers={"content-type": content_type}, content=b"ok")
    )
    page = await fetcher(server, {"a.test": [PUBLIC]}).fetch("http://a.test/")
    assert page.content_type == content_type.split(";", maxsplit=1)[0]
    assert page.charset == ("iso-8859-1" if "iso" in content_type else None)


async def test_an_error_status_comes_back_for_the_caller_to_judge_with_no_body() -> None:
    for status in (401, 403, 404, 429):
        server = Server(
            lambda r, s=status: httpx.Response(s, headers=HTML, content=b"<p>denied</p>")
        )
        page = await fetcher(server, {"a.test": [PUBLIC]}).fetch("http://a.test/")
        assert page.status_code == status
        assert page.body == b""


async def test_a_server_error_is_retried_once_then_fails() -> None:
    attempts = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, headers=HTML, content=b"back")

    page = await fetcher(Server(flaky), {"a.test": [PUBLIC]}).fetch("http://a.test/")
    assert page.body == b"back"
    assert attempts["n"] == 2

    down = Server(lambda r: httpx.Response(500))
    with pytest.raises(FetchFailedError) as caught:
        await fetcher(down, {"a.test": [PUBLIC]}).fetch("http://a.test/")
    assert caught.value.reason == "server_error"
    assert len(down.requests) == 2


async def test_a_dropped_connection_is_retried_once_then_fails() -> None:
    def drop(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    server = Server(drop)
    with pytest.raises(FetchFailedError) as caught:
        await fetcher(server, {"a.test": [PUBLIC]}).fetch("http://a.test/")
    assert caught.value.reason == "connection"
    assert len(server.requests) == 2


async def test_a_response_that_runs_past_the_deadline_is_cut_off() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, headers=HTML, content=b"late")

    transport = httpx.MockTransport(slow)
    f = SafeFetcher(
        FetchPolicy(timeout_s=0.3), resolver=resolver({"a.test": [PUBLIC]}), transport=transport
    )
    started = asyncio.get_running_loop().time()
    with pytest.raises(FetchFailedError) as caught:
        await f.fetch("http://a.test/")
    assert caught.value.reason == "timeout"
    assert asyncio.get_running_loop().time() - started < 2


# ------------------------------------------------------- no cookies, no scripts, small logs


async def test_no_cookie_is_sent_or_kept_even_across_a_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                302, headers={"location": "/next", "set-cookie": "sid=abc; Path=/"}
            )
        return httpx.Response(200, headers=HTML, content=b"ok")

    server = Server(handler)
    await fetcher(server, {"a.test": [PUBLIC]}).fetch("http://a.test/")
    assert all(
        "cookie" not in r.headers and "authorization" not in r.headers for r in server.requests
    )


async def test_the_request_identifies_itself_and_asks_only_for_what_it_decodes() -> None:
    server = Server()
    await fetcher(server, {"a.test": [PUBLIC]}).fetch("http://a.test/")
    headers = server.requests[0].headers
    assert headers["user-agent"].startswith("SecondMind-LinkReader")
    assert headers["accept-encoding"] == "gzip, deflate"


async def test_logs_show_the_host_and_never_the_path_or_query() -> None:
    server = Server()
    with capture_logs() as logs:
        await fetcher(server, {"a.test": [PUBLIC]}).fetch(
            "http://a.test/very/secret/path?token=hunter2"
        )
        with pytest.raises(FetchRefusedError):
            await fetcher(server, {}).fetch("http://10.0.0.5/also/secret?token=hunter2")
    text = " ".join(str(entry) for entry in logs)
    assert "a.test" in text
    assert "secret" not in text
    assert "hunter2" not in text


# ------------------------------------------------------------------ the test-only allowance


async def test_an_allowed_host_may_be_private_and_on_another_port_and_nothing_else_may() -> None:
    server = Server()
    policy = {"allow_hosts": frozenset({"fixtures"})}
    page = await fetcher(server, {"fixtures": ["172.18.0.5"]}, **policy).fetch(
        "http://fixtures:9000/a"
    )
    assert page.body == b"<p>hi</p>"
    assert server.requests[0].url.host == "172.18.0.5"
    with pytest.raises(FetchRefusedError):  # another private host stays refused
        await fetcher(server, {"other": ["172.18.0.6"]}, **policy).fetch("http://other:9000/a")
