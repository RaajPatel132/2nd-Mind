"""R.12, S4.4: a server-sent stream survives the web tier's proxy, and the production edge in front
of it, when the API heartbeats, and only then. And a deploy is a blip: while the API restarts the
edge holds a request instead of failing it.

nginx (the web image) fronts a stub upstream that behaves like the API's turn stream: one frame,
a long quiet stretch, one frame. With a heartbeat comment every 15 s the stream idles for 75 s
(longer than an ALB's 60 s idle timeout) and arrives complete. With no heartbeat and a short
read timeout, the same quiet stretch cuts the stream: the control that says the heartbeat is
what carries it. The edge is the real Caddy, with the repository's own Caddyfile and site file
(TLS from its local CA). The images come from ``make up`` / ``make up-prodlike`` (``WEB_IMAGE`` and
``API_IMAGE`` name them); the test skips when they are not built.
"""

import os
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

WEB_IMAGE = os.environ.get("WEB_IMAGE", "secondmind-web:local")
API_IMAGE = os.environ.get("API_IMAGE", "secondmind-api:local")
CADDY_IMAGE = "caddy:2.11.4-alpine"
EDGE_DIR = Path(__file__).resolve().parents[3] / "infra" / "edge"

# A stub API: /v1/quiet?heartbeat=S&idle=S sends one frame, then idles (with a comment every
# `heartbeat` seconds when it is > 0), then the closing frame. Chunked, as the real API streams.
UPSTREAM = r"""
import asyncio, urllib.parse

def chunk(text):
    data = text.encode()
    return b"%x\r\n" % len(data) + data + b"\r\n"

async def handle(reader, writer):
    request = (await reader.readuntil(b"\r\n\r\n")).decode()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.split()[1]).query)
    heartbeat = float(query.get("heartbeat", ["0"])[0])
    idle = float(query.get("idle", ["0"])[0])
    head = "HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n"
    head += "transfer-encoding: chunked\r\n\r\n"
    writer.write(head.encode())
    writer.write(chunk("event: turn.started\ndata: {}\n\n"))
    await writer.drain()
    waited = 0.0
    while waited < idle:
        step = min(heartbeat or idle, idle - waited)
        await asyncio.sleep(step)
        waited += step
        if heartbeat and waited < idle:
            writer.write(chunk(": keepalive\n\n"))
            await writer.drain()
    writer.write(chunk("event: turn.completed\ndata: {}\n\n") + b"0\r\n\r\n")
    await writer.drain()
    writer.close()

async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", 8000)
    async with server:
        await server.serve_forever()

asyncio.run(main())
"""


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def have(image: str) -> bool:
    return bool(run("images", "-q", image, check=False))


@dataclass
class Stack:
    """The web tier (and, with ``edge``, Caddy in front of it) on a private network."""

    name: str
    base: str
    verify: bool

    def start_upstream(self) -> None:
        run(
            "run", "-d", "--rm", "--name", f"{self.name}-up", "--network", self.name, API_IMAGE,
            "python", "-c", UPSTREAM,
        )  # fmt: skip

    def stop_upstream(self) -> None:
        run("rm", "-f", f"{self.name}-up", check=False)


def _wait_for(base: str, verify: bool) -> None:
    for _ in range(80):  # the edge and nginx up, and the upstream behind them answering
        try:
            if httpx.get(f"{base}/v1/quiet?idle=0", timeout=3, verify=verify).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)


@contextmanager
def proxied_stub(read_timeout: str, *, edge: bool = False) -> Iterator[Stack]:
    """nginx in front of the stub upstream, on a private network; with ``edge``, Caddy in front of
    nginx as it is in production. Yields the URL to call."""
    name = f"sm-sse-{uuid.uuid4().hex[:6]}"
    run("network", "create", name)
    stack = Stack(name=name, base="", verify=True)
    try:
        stack.start_upstream()
        # The edge's site file reaches the web tier as `web`.
        run(
            "run", "-d", "--rm", "--name", f"{name}-web", "--network", name,
            "--network-alias", "web", "-p", "0:8080",
            "-e", f"API_UPSTREAM=http://{name}-up:8000",
            "-e", f"NGINX_READ_TIMEOUT={read_timeout}", WEB_IMAGE,
        )  # fmt: skip
        if edge:
            run(
                "run", "-d", "--rm", "--name", f"{name}-edge", "--network", name, "-p", "0:443",
                "-e", "APP_HOST=localhost",
                "-v", f"{EDGE_DIR}/Caddyfile:/etc/caddy/Caddyfile:ro",
                "-v", f"{EDGE_DIR}/sites:/etc/caddy/sites:ro", CADDY_IMAGE,
            )  # fmt: skip
            port = run("port", f"{name}-edge", "443/tcp").splitlines()[0].rsplit(":", 1)[1]
            stack.base, stack.verify = f"https://localhost:{port}", False  # Caddy's local CA
        else:
            port = run("port", f"{name}-web", "8080/tcp").splitlines()[0].rsplit(":", 1)[1]
            stack.base = f"http://127.0.0.1:{port}"
        _wait_for(stack.base, stack.verify)
        yield stack
    finally:
        run("rm", "-f", f"{name}-edge", f"{name}-web", f"{name}-up", check=False)
        run("network", "rm", name, check=False)


def read_stream(stack: Stack, path: str) -> tuple[str, float, float]:
    """The whole stream, how long it took, and how soon the first bytes came."""
    started = time.monotonic()
    text, first = "", 0.0
    try:
        with httpx.stream(
            "GET", f"{stack.base}{path}", timeout=httpx.Timeout(150, connect=5), verify=stack.verify
        ) as response:
            for piece in response.iter_text():
                first = first or time.monotonic() - started
                text += piece
    except httpx.HTTPError:
        pass  # a stream cut by the proxy
    return text, time.monotonic() - started, first


@pytest.mark.skipif(not (have(WEB_IMAGE) and have(API_IMAGE)), reason="build the images first")
def test_a_stream_idle_for_75_seconds_arrives_complete_with_heartbeats() -> None:
    with proxied_stub("60s") as stack:
        text, took, _ = read_stream(stack, "/v1/quiet?heartbeat=15&idle=75")
    assert took >= 75
    assert text.count(": keepalive") >= 4
    assert text.startswith("event: turn.started")
    assert "event: turn.completed" in text


@pytest.mark.skipif(not (have(WEB_IMAGE) and have(API_IMAGE)), reason="build the images first")
def test_the_same_quiet_stretch_without_heartbeats_is_cut_by_the_proxy() -> None:
    with proxied_stub("3s") as stack:
        text, took, _ = read_stream(stack, "/v1/quiet?heartbeat=0&idle=9")
    assert "event: turn.started" in text
    assert "event: turn.completed" not in text
    assert took < 9


def _edge_available() -> bool:
    return have(WEB_IMAGE) and have(API_IMAGE) and (EDGE_DIR / "Caddyfile").exists()


@pytest.mark.skipif(not _edge_available(), reason="build the images first")
def test_a_stream_idle_for_75_seconds_arrives_complete_through_the_production_edge() -> None:
    # Caddy in front of nginx, as in production: TLS, two proxy hops, no buffering, no compression.
    with proxied_stub("60s", edge=True) as stack:
        text, took, first = read_stream(stack, "/v1/quiet?heartbeat=15&idle=75")
    assert first < 3, "the first frame must arrive at once, not when the stream ends"
    assert took >= 75
    assert text.count(": keepalive") >= 4
    assert text.startswith("event: turn.started")
    assert "event: turn.completed" in text


@pytest.mark.skipif(not _edge_available(), reason="build the images first")
def test_a_deploy_is_a_blip_steady_traffic_sees_no_error_while_the_api_restarts() -> None:
    """Measured, not promised (runbook section 5): with requests arriving steadily, a few seconds
    with no api behind the web tier cost nobody an error. The edge marks the web tier down within
    a second (it checks /readyz), holds what arrives, and lets it through when the api is back.
    (A single request after a long quiet spell can still meet a 502 in the first second or two,
    before the edge has noticed.)"""
    results: list[tuple[int | str, float]] = []
    stop = threading.Event()

    def poll(stack: Stack) -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                status: int | str = httpx.get(
                    f"{stack.base}/v1/quiet?idle=0", timeout=25, verify=stack.verify
                ).status_code
            except httpx.HTTPError as exc:
                status = type(exc).__name__
            results.append((status, time.monotonic() - started))
            time.sleep(0.2)

    with proxied_stub("60s", edge=True) as stack:
        poller = threading.Thread(target=poll, args=(stack,))
        poller.start()
        time.sleep(2)
        stack.stop_upstream()  # the api is stopped...
        time.sleep(3.3)
        stack.start_upstream()  # ...and started again
        time.sleep(10)
        stop.set()
        poller.join(timeout=30)
    failed = [r for r in results if r[0] != 200]
    assert len(results) > 20
    assert not failed, f"requests failed during the restart: {failed}"
    assert max(d for _, d in results) < 15  # the edge's hold, never longer
