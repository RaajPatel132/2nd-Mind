"""S4.6: the safe fetcher against real servers, on real sockets. Each attack is either refused
before any request reaches the server, or cut off mid-request: a page on a private address, a
redirect to one, a slow response that runs past the timeout, a gzip bomb, and a name that resolves
to a private address. (The servers are on loopback, so the first hop is let in with the test-only
allowance; everything a redirect or a name leads to is judged by the real rules.)"""

import asyncio
import gzip
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest

from secondmind.links import FetchRefusedError
from secondmind.links.adapters import FetchFailedError, FetchPolicy, SafeFetcher

pytestmark = pytest.mark.integration

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter, str], Awaitable[None]]


class Stats:
    def __init__(self) -> None:
        self.connections = 0
        self.paths: list[str] = []


@asynccontextmanager
async def serve(handler: Handler) -> AsyncIterator[tuple[int, Stats]]:
    stats = Stats()

    async def on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        stats.connections += 1
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            path = head.split(" ", 2)[1]
            stats.paths.append(path)
            await handler(reader, writer, path)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, stats
    finally:
        server.close()
        await server.wait_closed()


def respond(status: str = "200 OK", headers: str = "", body: bytes = b"") -> bytes:
    return (
        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n{headers}\r\n"
    ).encode() + body


def fetcher(**policy: object) -> SafeFetcher:
    return SafeFetcher(FetchPolicy(allow_hosts=frozenset({"127.0.0.1"}), **policy))  # type: ignore[arg-type]


async def page(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: str) -> None:
    writer.write(respond(headers="Content-Type: text/html\r\n", body=b"<p>hello</p>"))
    await writer.drain()


async def test_a_page_on_a_private_address_is_refused_before_any_request() -> None:
    async with serve(page) as (port, stats):
        # No allowance: loopback, private and link-local literals, and the address spellings.
        for host in ("127.0.0.1", "127.1", "2130706433", "0x7f.1", "localhost", "[::1]"):
            with pytest.raises(FetchRefusedError):
                await SafeFetcher().fetch(f"http://{host}:{port}/")
        for literal in ("10.0.0.5", "169.254.169.254", "192.168.1.1", "100.64.0.1"):
            with pytest.raises(FetchRefusedError):
                await SafeFetcher().fetch(f"http://{literal}/")
    assert stats.connections == 0  # the server never saw a connection


async def test_the_test_only_allowance_lets_the_fixture_server_through_and_nothing_else() -> None:
    async with serve(page) as (port, stats):
        fetched = await fetcher().fetch(f"http://127.0.0.1:{port}/a")
        assert fetched.body == b"<p>hello</p>"
        assert stats.connections == 1


async def test_a_redirect_to_a_private_address_is_refused_before_a_second_request() -> None:
    async def redirect(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: str
    ) -> None:
        target = (
            "http://169.254.169.254/latest/meta-data/" if path == "/meta" else "http://localhost/"
        )
        writer.write(respond("302 Found", f"Location: {target}\r\n"))
        await writer.drain()

    async with serve(redirect) as (port, stats):
        with pytest.raises(FetchRefusedError) as metadata:
            await fetcher().fetch(f"http://127.0.0.1:{port}/meta")
        assert metadata.value.rule == "link_local"
        with pytest.raises(FetchRefusedError) as local:
            await fetcher().fetch(f"http://127.0.0.1:{port}/other")
        assert local.value.rule == "loopback"
    assert stats.paths == ["/meta", "/other"]  # one request each; the redirect targets got none


async def test_a_name_that_resolves_to_a_private_address_is_refused_before_any_request() -> None:
    async def resolve_private(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]  # what a name controlled by an attacker would answer

    async with serve(page) as (_, stats):
        f = SafeFetcher(FetchPolicy(), resolver=resolve_private)
        with pytest.raises(FetchRefusedError) as caught:
            await f.fetch("http://evil.example/")  # port 80: refused on the address, not the port
        assert caught.value.rule == "loopback"
    assert stats.connections == 0


async def test_a_slow_response_is_cut_off_at_the_timeout() -> None:
    async def stall(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: str) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<p>start")
        await writer.drain()
        await asyncio.sleep(3)  # then nothing (longer than the 1 s timeout)

    async with serve(stall) as (port, _):
        started = time.monotonic()
        with pytest.raises(FetchFailedError) as caught:
            await fetcher(timeout_s=1.0).fetch(f"http://127.0.0.1:{port}/")
        assert caught.value.reason == "timeout"
        assert time.monotonic() - started < 4


async def test_a_body_that_trickles_in_forever_is_cut_off_by_the_whole_fetch_deadline() -> None:
    async def trickle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: str
    ) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n")
        for _ in range(20):
            writer.write(b"x")
            await writer.drain()
            await asyncio.sleep(0.2)  # each byte is "in time" for a per-read timeout

    async with serve(trickle) as (port, _):
        started = time.monotonic()
        with pytest.raises(FetchFailedError) as caught:
            await fetcher(timeout_s=1.5).fetch(f"http://127.0.0.1:{port}/")
        assert caught.value.reason == "timeout"
        assert time.monotonic() - started < 5


async def test_a_gzip_bomb_is_cut_off_at_the_size_limit() -> None:
    bomb = gzip.compress(b"\x00" * 60_000_000)  # 60 MB once decompressed, about 60 KB on the wire

    async def serve_bomb(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: str
    ) -> None:
        writer.write(
            respond("200 OK", "Content-Type: text/html\r\nContent-Encoding: gzip\r\n", bomb)
        )
        await writer.drain()

    async with serve(serve_bomb) as (port, _):
        started = time.monotonic()
        with pytest.raises(FetchRefusedError) as caught:
            await fetcher(max_bytes=1_000_000).fetch(f"http://127.0.0.1:{port}/")
        assert caught.value.rule == "too_large"
        assert time.monotonic() - started < 3


async def test_a_pdf_is_refused_by_its_type_before_its_body_is_read() -> None:
    async def pdf(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: str) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\n\r\n")
        writer.write(b"%PDF-1.7 " + b"x" * 1000)
        await writer.drain()

    async with serve(pdf) as (port, _):
        with pytest.raises(FetchRefusedError) as caught:
            await fetcher().fetch(f"http://127.0.0.1:{port}/doc.pdf")
    assert caught.value.rule == "content_type"
    assert caught.value.detail == "application/pdf"
