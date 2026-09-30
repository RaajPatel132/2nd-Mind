"""Reading a saved link (S4.7): fetch, classify, digest, chunk and embed. No database and no model
SDK: the collaborators are injected, so the whole path runs offline in tests and in the evals.

The outcomes follow the status table: ``full`` (main text found), ``partial`` (paywall, script-only,
blocked, too large or short, a PDF: whatever there is is kept), ``failed`` (the page couldn't be
opened) and ``refused`` (a safety rule or a limit: no request was made). Whatever the status, the
item can still be found by what the person said about it.
"""

import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from secondmind.core import WorkspaceScope
from secondmind.links.adapters.extract import ExtractedPage, extract_html, extract_plain
from secondmind.links.adapters.fetcher import FetchedPage, Fetcher, FetchFailedError
from secondmind.links.adapters.video import VideoInfo, read_video
from secondmind.links.chunking import chunk_text
from secondmind.links.datablock import data_block
from secondmind.links.digest import DigestOutput
from secondmind.links.model import ChunkRow, FetchStatus, LinkKind, LinkSource
from secondmind.links.urls import FetchRefusedError, site_name, video_site
from secondmind.policy import scan_secrets

MIN_WORDS_TO_KEEP = 10  # below this, a page has no text worth a digest or a chunk

RULE_LABELS: dict[str, str] = {
    "scheme": "not a web link",
    "port": "unusual port",
    "credentials": "it has a password in it",
    "url_too_long": "too long",
    "bad_url": "not a valid address",
    "loopback": "this server's own address",
    "private_address": "private address",
    "link_local": "link-local address",
    "carrier_grade_nat": "private address",
    "multicast": "multicast address",
    "reserved_address": "reserved address",
    "too_many_redirects": "too many redirects",
    "content_type": "not a web page",
    "too_many_links": "too many links in one message",
    "rate_limited": "too many links just now",
    "dns": "address didn't resolve",
}


class LinkSourceStore(Protocol):
    """What the read needs of the links table (``SqlLinkStore`` is the real one)."""

    async def by_item(self, item_id: UUID) -> LinkSource | None: ...

    async def update(self, item_id: UUID, **changes: Any) -> None: ...

    async def replace_chunks(self, item_id: UUID, chunks: Sequence[ChunkRow]) -> None: ...

    async def unembedded_chunks(self, limit: int = 200) -> list[tuple[UUID, UUID, str]]: ...

    async def set_embeddings(
        self, vectors: Sequence[tuple[UUID, list[float]]], model: str
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class LinkReading:
    """Everything the worker needs to read a saved link: the safe fetcher, the links table for a
    workspace, and the read's settings."""

    fetcher: Fetcher
    stores: Callable[[WorkspaceScope], LinkSourceStore]
    settings: "ReadSettings"


class DigestFn(Protocol):
    async def __call__(
        self, *, source: str, person_said: str, material: str
    ) -> DigestOutput | None:
        """The digest step's output for ``material`` (a data block), or None when the model's
        answer was invalid (the page's own metadata is used instead)."""
        ...


EmbedFn = Callable[[Sequence[str]], Awaitable[list[list[float]] | None]]


@dataclass(frozen=True, slots=True)
class ReadSettings:
    chunk_tokens: int = 300
    max_chunks: int = 40
    digest_chars: int = 12_000
    embedding_model: str | None = None


@dataclass(slots=True)
class ReadResult:
    status: FetchStatus
    message: str
    reason: str | None = None
    rule: str | None = None
    title: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    cues: list[str] = field(default_factory=list)
    site: str | None = None
    author: str | None = None
    published: str | None = None
    description: str | None = None
    word_count: int | None = None
    final_host: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    bytes_read: int | None = None
    redirects: int | None = None
    extraction_method: str | None = None
    channel: str | None = None
    duration_s: int | None = None
    thumbnail_url: str | None = None
    chunks: list[ChunkRow] = field(default_factory=list)
    digested: bool = False  # a model wrote the title and summary (else the page's own were used)
    video: bool = False

    def source_changes(self, *, fetched_at: datetime) -> dict[str, object]:
        """The ``link_sources`` columns this outcome sets."""
        return {
            "fetch_status": self.status,
            "fetch_reason": self.reason,
            "site": self.site,
            "author": self.author,
            "published_at": _parse_date(self.published),
            "description": self.description,
            "word_count": self.word_count,
            "final_host": self.final_host,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "bytes_read": self.bytes_read,
            "redirects": self.redirects,
            "extraction_method": self.extraction_method,
            "channel": self.channel,
            "duration_s": self.duration_s,
            "thumbnail_url": self.thumbnail_url,
            "fetched_at": fetched_at,
            "detail": {"rule": self.rule, "digested": self.digested},
        }


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    from dateutil import parser  # noqa: PLC0415

    try:
        parsed = (
            parser.isoparse(value) if re.match(r"^\d{4}-\d\d-\d\d", value) else parser.parse(value)
        )
    except (ValueError, OverflowError):
        return None
    from datetime import UTC  # noqa: PLC0415

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def refused_result(rule: str, message: str | None = None) -> ReadResult:
    label = RULE_LABELS.get(rule, message or "not allowed")
    return ReadResult(
        status=FetchStatus.REFUSED,
        rule=rule,
        reason=label,
        message=f"Didn't open it: {label}.",
    )


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class LinkReader:
    def __init__(
        self,
        fetcher: Fetcher,
        digest: DigestFn,
        embed: EmbedFn,
        settings: ReadSettings | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._digest = digest
        self._embed = embed
        self._settings = settings or ReadSettings()

    # ------------------------------------------------------------------ a link

    async def read(self, source: LinkSource, *, person_said: str) -> ReadResult:
        """Read ``source.url``. Never raises for a page that can't be read: that is a status.
        A refusal of the spend gate (a model step) does propagate: the job is deferred."""
        site = video_site(source.url) if source.kind is LinkKind.VIDEO else None
        if site is not None:
            return await self._read_video(source, site, person_said)
        try:
            fetched = await self._fetcher.fetch(source.url)
        except FetchRefusedError as exc:
            if exc.rule == "too_large":
                return self._partial("too large", host=None)
            if exc.rule == "content_type" and exc.detail == "application/pdf":
                return self._partial("PDF", host=None)
            return refused_result(exc.rule, exc.message)
        except FetchFailedError as exc:
            return ReadResult(
                status=FetchStatus.FAILED,
                reason=exc.reason,
                message="Couldn't open it. Saved what you said.",
            )
        return await self._from_page(source, fetched, person_said)

    async def _from_page(
        self, source: LinkSource, fetched: FetchedPage, person_said: str
    ) -> ReadResult:
        code = fetched.status_code
        if code in (401, 403, 429):
            reasons = {
                401: "login required (HTTP 401)",
                403: "blocked (HTTP 403)",
                429: "too many requests (HTTP 429)",
            }
            return _stamp(self._partial(reasons[code], host=fetched.final_host), fetched)
        if code >= 400:
            failed = ReadResult(
                status=FetchStatus.FAILED,
                reason="not found" if code in (404, 410) else f"HTTP {code}",
                message="Couldn't open it. Saved what you said.",
            )
            return _stamp(failed, fetched)
        page = (
            extract_plain(_text(fetched))
            if fetched.content_type == "text/plain"
            else extract_html(fetched.body, fetched.charset)
        )
        result = _stamp(
            ReadResult(
                status=FetchStatus.FULL,
                message="",
                bytes_read=len(fetched.body),
                extraction_method=page.method,
                word_count=page.word_count,
                site=page.site or site_name(fetched.final_host),
                author=page.author or None,
                published=page.published or None,
                description=page.description or None,
                thumbnail_url=page.image_url or None,
            ),
            fetched,
        )
        reason = self._why_partial(page)
        if reason is not None:
            result.status, result.reason = FetchStatus.PARTIAL, reason
        await self._finish(result, source, page, person_said, "a web page")
        return result

    @staticmethod
    def _why_partial(page: ExtractedPage) -> str | None:
        if page.paywall:
            return "paywall"
        if page.script_only:
            return "needs JavaScript"
        if page.too_short:
            return "too short"
        return None

    def _partial(self, reason: str, *, host: str | None) -> ReadResult:
        return ReadResult(
            status=FetchStatus.PARTIAL,
            reason=reason,
            message=f"Could only read part of it ({reason}). Add the text?",
            final_host=host,
        )

    # ------------------------------------------------------------------ a video

    async def _read_video(self, source: LinkSource, site: str, person_said: str) -> ReadResult:
        info = await read_video(self._fetcher, source.url, site)
        if not info.found:
            return self._partial("couldn't read the video's page", host=None)
        result = ReadResult(
            status=FetchStatus.FULL,
            message="",
            site=site.capitalize(),
            channel=info.channel or None,
            duration_s=info.duration_s,
            thumbnail_url=info.thumbnail_url or None,
            description=info.description or None,
            published=info.published or None,
            extraction_method="oembed",
            video=True,
            title=info.title,
        )
        material = "\n".join(
            part
            for part in (
                f"Title: {info.title}",
                f"Channel: {info.channel}" if info.channel else "",
                f"Duration: {format_duration(info.duration_s)}" if info.duration_s else "",
                f"Description: {info.description}" if info.description else "",
            )
            if part
        )
        digest = await self._digest(
            source=f"a video on {site.capitalize()} (no transcript)",
            person_said=person_said,
            material=data_block("video page metadata", material),
        )
        self._apply_digest(
            result, digest, fallback_title=info.title, fallback_summary=info.description
        )
        result.message = " · ".join(
            p for p in (f"Read {result.title}", info.channel, format_duration(info.duration_s)) if p
        )
        return result

    # ---------------------------------------------------------- pasted text ("Add the text")

    async def read_text(self, source: LinkSource, *, person_said: str, text: str) -> ReadResult:
        """Text the person pasted for a link that could only be read in part: the same digest and
        chunking, and it counts as content, not as something the person said (ADR-0037)."""
        page = extract_plain(text)
        result = ReadResult(
            status=FetchStatus.FULL,
            message="",
            word_count=page.word_count,
            extraction_method="pasted",
            final_host=None,
        )
        if page.word_count < MIN_WORDS_TO_KEEP:
            result.status, result.reason = FetchStatus.PARTIAL, "too short"
            result.message = "That was too short to read. Add more of the text?"
            return result
        await self._finish(result, source, page, person_said, "text the person pasted for a link")
        return result

    # ------------------------------------------------------------------ digest, chunks, message

    async def _finish(
        self,
        result: ReadResult,
        source: LinkSource,
        page: ExtractedPage,
        person_said: str,
        what: str,
    ) -> None:
        has_text = page.word_count >= MIN_WORDS_TO_KEEP
        digest: DigestOutput | None = None
        if has_text or page.title or page.description:
            digest = await self._digest(
                source=what, person_said=person_said, material=self._material(page)
            )
        self._apply_digest(
            result,
            digest,
            fallback_title=page.title or result.final_host or source.url,
            fallback_summary=page.description or (page.text[:280] if has_text else ""),
        )
        if has_text:
            result.chunks = await self._chunks(result.title, page.text)
        if result.status is FetchStatus.FULL:
            words = f"{page.word_count:,} words"
            result.message = " · ".join(
                p for p in (f"Read {result.title}", result.site, words) if p
            )
        elif result.message == "":
            result.message = f"Could only read part of it ({result.reason}). Add the text?"

    def _material(self, page: ExtractedPage) -> str:
        head = "\n".join(
            part
            for part in (
                f"Title: {page.title}" if page.title else "",
                f"Site: {page.site}" if page.site else "",
                f"Author: {page.author}" if page.author else "",
                f"Published: {page.published}" if page.published else "",
                f"Description: {page.description}" if page.description else "",
            )
            if part
        )
        body = page.text[: self._settings.digest_chars]
        return data_block("page text", f"{head}\n\n{body}".strip())

    @staticmethod
    def _apply_digest(
        result: ReadResult,
        digest: DigestOutput | None,
        *,
        fallback_title: str,
        fallback_summary: str,
    ) -> None:
        """The digest's fields, unless any of them holds a secret (a page can try to plant one
        through an obedient model): then the page's own title and description stand in. Nothing a
        page says is stored with a password, PIN or key in it (ADR-0021)."""
        if digest is not None and _has_secret(
            digest.title, digest.summary, *digest.tags, *digest.cues
        ):
            digest = None
        if digest is not None:
            result.title = digest.title.strip() or _scrubbed(fallback_title)
            result.summary = digest.summary.strip() or _scrubbed(fallback_summary)
            result.tags = [t.strip().lower() for t in digest.tags if t.strip()][:6]
            result.cues = [c.strip() for c in digest.cues if c.strip()][:3]
            result.digested = True
        else:
            result.title = _scrubbed(fallback_title)
            result.summary = _scrubbed(fallback_summary)

    async def _chunks(self, title: str, text: str) -> list[ChunkRow]:
        pieces = chunk_text(
            text, tokens=self._settings.chunk_tokens, max_chunks=self._settings.max_chunks
        )
        if not pieces:
            return []
        vectors = await self._embed([f"{title}\n\n{c.text}" for c in pieces])
        model = self._settings.embedding_model if vectors is not None else None
        return [
            ChunkRow(
                id=uuid4(),
                position=c.position,
                text=_scrubbed(c.text),
                content_hash=hashlib.sha256(_scrubbed(c.text).encode("utf-8")).hexdigest(),
                embedding=None if vectors is None else vectors[i],
                embedding_model=model,
            )
            for i, c in enumerate(pieces)
        ]


def _has_secret(*texts: str) -> bool:
    return any(scan_secrets(t).hits for t in texts if t)


def _scrubbed(text: str) -> str:
    """``text`` with any secret it holds redacted."""
    return scan_secrets(text).redacted if text else text


def _stamp(result: ReadResult, fetched: FetchedPage) -> ReadResult:
    """What the fetch itself learned, whatever the outcome (the glass box shows it)."""
    result.final_host = fetched.final_host
    result.status_code = fetched.status_code
    result.content_type = fetched.content_type
    result.redirects = fetched.redirects
    return result


def _text(fetched: FetchedPage) -> str:
    return fetched.body.decode(fetched.charset or "utf-8", "replace")


__all__ = [
    "RULE_LABELS",
    "DigestFn",
    "EmbedFn",
    "LinkReader",
    "LinkReading",
    "LinkSourceStore",
    "ReadResult",
    "ReadSettings",
    "VideoInfo",
    "format_duration",
    "refused_result",
]
