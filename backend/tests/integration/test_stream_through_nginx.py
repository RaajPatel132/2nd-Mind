"""R.12: a server-sent stream survives the web tier's proxy when the API heartbeats, and only then.

nginx (the web image) fronts a stub upstream that behaves like the API's turn stream: one frame,
a long quiet stretch, one frame. With a heartbeat comment every 15 s the stream idles for 75 s
(longer than an ALB's 60 s idle timeout) and arrives complete. With no heartbeat and a short
read timeout, the same quiet stretch cuts the stream: the control that says the heartbeat is
what carries it. The images come from ``make up`` / ``make up-prodlike`` (``WEB_IMAGE`` and
``API_IMAGE`` name them); the test skips when they are not built.
"""

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest

pytestmark = pytest.mark.integration

WEB_IMAGE = os.environ.get("WEB_IMAGE", "secondmind-web:local")
API_IMAGE = os.environ.get("API_IMAGE", "secondmind-api:local")

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


@contextmanager
def proxied_stub(read_timeout: str) -> Iterator[str]:
    """nginx in front of the stub upstream, on a private network; yields nginx's base URL."""
    name = f"sm-sse-{uuid.uuid4().hex[:6]}"
    run("network", "create", name)
    try:
        run(
            "run", "-d", "--rm", "--name", f"{name}-up", "--network", name, API_IMAGE,
            "python", "-c", UPSTREAM,
        )  # fmt: skip
        run(
            "run", "-d", "--rm", "--name", f"{name}-web", "--network", name, "-p", "0:8080",
            "-e", f"API_UPSTREAM=http://{name}-up:8000",
            "-e", f"NGINX_READ_TIMEOUT={read_timeout}", WEB_IMAGE,
        )  # fmt: skip
        port = run("port", f"{name}-web", "8080/tcp").splitlines()[0].rsplit(":", 1)[1]
        yield_base = f"http://127.0.0.1:{port}"
        for _ in range(60):  # nginx up, and the upstream behind it answering
            try:
                if httpx.get(f"{yield_base}/v1/quiet?idle=0", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        yield yield_base
    finally:
        run("rm", "-f", f"{name}-web", f"{name}-up", check=False)
        run("network", "rm", name, check=False)


def read_stream(base: str, path: str) -> tuple[str, float]:
    started = time.monotonic()
    text = ""
    try:
        with httpx.stream(
            "GET", f"{base}{path}", timeout=httpx.Timeout(150, connect=5)
        ) as response:
            for piece in response.iter_text():
                text += piece
    except httpx.HTTPError:
        pass  # a stream cut by the proxy
    return text, time.monotonic() - started


@pytest.mark.skipif(not (have(WEB_IMAGE) and have(API_IMAGE)), reason="build the images first")
def test_a_stream_idle_for_75_seconds_arrives_complete_with_heartbeats() -> None:
    with proxied_stub("60s") as base:
        text, took = read_stream(base, "/v1/quiet?heartbeat=15&idle=75")
    assert took >= 75
    assert text.count(": keepalive") >= 4
    assert text.startswith("event: turn.started")
    assert "event: turn.completed" in text


@pytest.mark.skipif(not (have(WEB_IMAGE) and have(API_IMAGE)), reason="build the images first")
def test_the_same_quiet_stretch_without_heartbeats_is_cut_by_the_proxy() -> None:
    with proxied_stub("3s") as base:
        text, took = read_stream(base, "/v1/quiet?heartbeat=0&idle=9")
    assert "event: turn.started" in text
    assert "event: turn.completed" not in text
    assert took < 9
