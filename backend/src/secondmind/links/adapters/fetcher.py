"""The safe fetcher (S4.6, FR-2.8, NFR-1.2): the only way the app requests a URL a person gave.

For every hop (the URL, then each redirect as a new URL):

1. the URL is parsed and checked (`links.urls`): scheme, port, credentials, address spellings;
2. the host is resolved **once**, every address it resolved to is checked, and the connection goes
   to that checked address (the URL's host is replaced by it, with the original name kept for the
   ``Host`` header and TLS), so DNS rebinding can't swap the address between check and connect;
3. the response is read as a stream, cut off past ``max_bytes`` *after decompression* (a gzip bomb
   stops at the limit), after a connect timeout and within one whole-fetch deadline.

Page scripts never run (nothing here executes anything), and no cookies are sent or kept: a fresh
client per fetch, cleared after every hop. Logs carry the host and never the path or query.
"""

import asyncio
import contextlib
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from secondmind.links.urls import (
    FetchRefusedError,
    ParsedUrl,
    check_resolved,
    host_for_log,
    parse_url,
)
from secondmind.observability import get_logger

log = get_logger(__name__)

Resolver = Callable[[str, int], Awaitable[list[str]]]
REDIRECTS = frozenset({301, 302, 303, 307, 308})
ACCEPTED_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
USER_AGENT = "SecondMind-LinkReader/1.0 (saves a page a person pasted; no scripts, no cookies)"


class FetchFailedError(Exception):
    """The request couldn't be completed: DNS, connection or timeout errors, or a server error
    that persisted after one retry. Not a safety refusal: the page may simply be down."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    timeout_s: float = 10.0
    max_bytes: int = 2_000_000
    max_redirects: int = 5
    # Test-only (LINK_ALLOW_PRIVATE_HOSTS): hosts exempt from the address rules.
    allow_hosts: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """What came back. ``status_code`` may be an error (401, 403, 404, 429, 5xx): the caller
    decides what that means for the link (partial or failed); ``body`` is empty then."""

    url: str
    final_url: str
    final_host: str
    status_code: int
    content_type: str
    charset: str | None
    body: bytes
    redirects: int
    hosts: tuple[str, ...]  # every host touched, in order (the glass box shows the host only)


class Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _content_type(response: httpx.Response) -> tuple[str, str | None]:
    raw = response.headers.get("content-type", "")
    main = raw.split(";", 1)[0].strip().lower()
    charset = None
    for part in raw.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset":
            charset = value.strip("\"' ").lower() or None
    return main, charset


class SafeFetcher:
    """Fetch a page a person linked to, or refuse. ``transport`` and ``resolver`` are for tests."""

    def __init__(
        self,
        policy: FetchPolicy | None = None,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._policy = policy or FetchPolicy()
        self._resolve = resolver
        self._transport = transport

    async def fetch(self, url: str) -> FetchedPage:
        policy = self._policy
        try:
            async with asyncio.timeout(policy.timeout_s):
                return await self._follow(url)
        except TimeoutError as exc:
            log.info("links.timeout", host=host_for_log(url))
            raise FetchFailedError("timeout", "The page took too long to answer.") from exc

    # ------------------------------------------------------------------ redirects

    async def _follow(self, url: str) -> FetchedPage:
        policy = self._policy
        current, hosts = url, []
        async with self._client() as client:
            for redirects in range(policy.max_redirects + 1):
                parsed = parse_url(current, allow_hosts=policy.allow_hosts)  # each hop, anew
                hosts.append(parsed.host)
                response, body = await self._hop(client, parsed)
                client.cookies.clear()  # none is kept, even within one fetch
                location = response.headers.get("location")
                if response.status_code in REDIRECTS and location:
                    current = str(httpx.URL(current).join(location))
                    continue
                content_type, charset = _content_type(response)
                log.info(
                    "links.fetched",
                    host=parsed.host,
                    status=response.status_code,
                    bytes=len(body),
                    redirects=redirects,
                )
                return FetchedPage(
                    url=url,
                    final_url=current,
                    final_host=parsed.host,
                    status_code=response.status_code,
                    content_type=content_type,
                    charset=charset,
                    body=body,
                    redirects=redirects,
                    hosts=tuple(hosts),
                )
        log.info("links.refused", host=host_for_log(current), rule="too_many_redirects")
        raise FetchRefusedError("too_many_redirects", "That link redirects too many times.")

    # ------------------------------------------------------------------ one hop

    def _client(self) -> httpx.AsyncClient:
        policy = self._policy
        return httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,  # we follow, and check every hop ourselves
            trust_env=False,  # no proxy or certificate settings from the environment
            timeout=httpx.Timeout(policy.timeout_s, connect=min(5.0, policy.timeout_s)),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
        )

    async def _address(self, parsed: ParsedUrl) -> str:
        """Resolve once and check every address; returns the one to connect to."""
        if parsed.is_ip_literal:
            return parsed.host
        try:
            addresses = await self._resolve(parsed.host, parsed.port)
        except OSError as exc:
            raise FetchFailedError("dns", "That address didn't resolve.") from exc
        if parsed.host not in self._policy.allow_hosts:
            try:
                check_resolved(addresses)
            except FetchRefusedError as exc:
                log.info("links.refused", host=parsed.host, rule=exc.rule)
                raise
        elif not addresses:
            raise FetchFailedError("dns", "That address didn't resolve.")
        return addresses[0]

    async def _hop(
        self, client: httpx.AsyncClient, parsed: ParsedUrl
    ) -> tuple[httpx.Response, bytes]:
        ip = await self._address(parsed)
        connect_host = f"[{ip}]" if ":" in ip else ip
        default = parsed.port == (443 if parsed.scheme == "https" else 80)
        headers = {
            "Host": parsed.host if default else f"{parsed.host}:{parsed.port}",
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "Accept-Encoding": "gzip, deflate",  # what httpx decodes here; nothing exotic
        }
        if ":" in parsed.host:
            headers["Host"] = f"[{parsed.host}]" + ("" if default else f":{parsed.port}")
        extensions: dict[str, str] = {}
        if parsed.scheme == "https" and not parsed.is_ip_literal:
            extensions["sni_hostname"] = parsed.host  # the certificate is checked for the name
        target = f"{parsed.scheme}://{connect_host}:{parsed.port}{parsed.path_query}"
        for attempt in range(2):  # one retry, for a dropped connection or a server error
            try:
                return await self._read(client, target, headers, extensions, parsed)
            except (httpx.TransportError, _ServerErrorError) as exc:
                if attempt == 1:
                    reason = "server_error" if isinstance(exc, _ServerErrorError) else "connection"
                    raise FetchFailedError(reason, "The page couldn't be opened.") from exc
                await asyncio.sleep(0.2)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _read(
        self,
        client: httpx.AsyncClient,
        target: str,
        headers: dict[str, str],
        extensions: dict[str, str],
        parsed: ParsedUrl,
    ) -> tuple[httpx.Response, bytes]:
        request = client.build_request("GET", target, headers=headers, extensions=extensions)
        response = await client.send(request, stream=True)
        try:
            if response.status_code >= 500:
                raise _ServerErrorError
            if response.status_code in REDIRECTS or response.status_code >= 400:
                return response, b""
            content_type, _ = _content_type(response)
            if content_type and content_type not in ACCEPTED_TYPES:
                log.info("links.refused", host=parsed.host, rule="content_type")
                raise FetchRefusedError(
                    "content_type", "That isn't a web page or plain text.", detail=content_type
                )
            announced = response.headers.get("content-length", "")
            if announced.isdigit() and int(announced) > self._policy.max_bytes * 50:
                raise FetchRefusedError("too_large", "That page is too large.")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():  # decoded: a gzip bomb stops here
                total += len(chunk)
                if total > self._policy.max_bytes:
                    log.info("links.refused", host=parsed.host, rule="too_large")
                    raise FetchRefusedError("too_large", "That page is too large.")
                chunks.append(chunk)
            return response, b"".join(chunks)
        finally:
            with contextlib.suppress(Exception):
                await response.aclose()


class _ServerErrorError(Exception):
    """A 5xx, retried once before it becomes a failure."""
