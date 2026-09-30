"""The link goldens (S4.7): thirteen ways of saving a link and five ways of finding what it held.

Each case in ``evals/cases/links/{ingest,recall}/*.yaml`` sends chat messages through the real turn
runner on Postgres, reads the saved links the way the worker does, and checks what was kept. The
pages are synthetic files in ``evals/fixtures/links/``, served by :class:`FixtureTransport`: a URL
that isn't listed fails like a dead host, and the suite checks nothing unknown was requested, so a
run never touches the internet. The case's ``digest`` is what a model that read the page well
would return; on the fake provider it is replayed, so a run checks the code around the model
(statuses, chunks, what is written, recall, citations). A live run sends the same pages to real
models.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from secondmind.agent import Turn, TurnRunner
from secondmind.agent.adapters import SqlTurnStore
from secondmind.config import (
    DEFAULT_RESOURCES_DIR,
    read_price_table,
    read_routing_file,
    resolve_routing,
)
from secondmind.core import (
    CitationsEvent,
    FetchEvent,
    ItemStatus,
    RetrievalEvent,
    WorkspaceScope,
)
from secondmind.corrections import correction_responders
from secondmind.evals.recall import eval_runner
from secondmind.ingestion import offline_responders, replay_key
from secondmind.links import (
    FetchedPage,
    FetchFailedError,
    FetchRefusedError,
    LinkReading,
    LinkSaver,
    LinkSource,
    ReadSettings,
    SaveSettings,
    canonical_url,
    extract_urls,
    without_links,
)
from secondmind.links.adapters import SqlLinkStore
from secondmind.memory import Memory
from secondmind.memory.adapters import Database, sql_memory
from secondmind.providers import (
    AdapterRequest,
    FakeProvider,
    FakeScript,
    ModelRouter,
    ResiliencePolicy,
)
from secondmind.retrieval import recall_responders, recall_text_responders

LINKS_DIR = DEFAULT_RESOURCES_DIR / "evals" / "cases" / "links"
FIXTURES_DIR = DEFAULT_RESOURCES_DIR / "evals" / "fixtures" / "links"
READ = ReadSettings(chunk_tokens=120, max_chunks=12)


# ------------------------------------------------------------------ the fixture transport


class FixtureTransport:
    """Answers each URL from ``fixtures/links/transport.yaml`` and keeps a log of every request."""

    def __init__(self, directory: Path = FIXTURES_DIR) -> None:
        raw = yaml.safe_load((directory / "transport.yaml").read_text(encoding="utf-8")) or {}
        self._dir = directory
        self._pages: Mapping[str, Mapping[str, Any]] = raw.get("pages", {})
        self._refused: Mapping[str, Mapping[str, Any]] = raw.get("refused", {})
        self._oembed: Mapping[str, Mapping[str, Any]] = raw.get("oembed", {})
        self.requests: list[str] = []
        self.unknown: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.requests.append(url)
        if url in self._refused:
            refusal = self._refused[url]
            raise FetchRefusedError(
                refusal["rule"], refusal.get("message", ""), refusal.get("detail", "")
            )
        video = self._video_for(url)
        if video is not None:
            return self._answer(url, self._oembed[video], "application/json")
        page = self._pages.get(url)
        if page is None:
            self.unknown.append(url)
            raise FetchFailedError("couldn't connect", "the fixture has no such page")
        return self._answer(url, page, "text/html")

    def _video_for(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.path not in ("/oembed", "/api/oembed.json"):
            return None
        target = parse_qs(parsed.query).get("url", [""])[0]
        return target if target in self._oembed else None

    def _answer(self, url: str, spec: Mapping[str, Any], content_type: str) -> FetchedPage:
        final = str(spec.get("final_url", url))
        host = urlparse(final).hostname or ""
        status = int(spec.get("status", 200))
        name = spec.get("file")
        body = (self._dir / "pages" / name).read_bytes() if name and status < 400 else b""
        return FetchedPage(
            url=url,
            final_url=final,
            final_host=host,
            status_code=status,
            content_type=str(spec.get("content_type", content_type)),
            charset="utf-8",
            body=body,
            redirects=int(spec.get("redirects", 0)),
            hosts=(host,),
        )


# ------------------------------------------------------------------ the cases


@dataclass(frozen=True, slots=True)
class LinkCase:
    id: str
    title: str
    suite: str  # "ingest" or "recall"
    now: datetime
    messages: tuple[str, ...]  # what the person sends, in order (a link each, or a few)
    digest: Mapping[str, Any] | None  # what a good model returns for the page
    model: Mapping[str, Any]  # the other steps' recorded outputs, for the last message / question
    question: str | None
    expect: Mapping[str, Any]
    path: Path


def load_cases(suite: str, directory: Path = LINKS_DIR) -> list[LinkCase]:
    cases = []
    for path in sorted((directory / suite).glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cases.append(
            LinkCase(
                id=str(raw.get("id", path.stem)),
                title=str(raw.get("title", path.stem)),
                suite=suite,
                now=datetime.fromisoformat(str(raw.get("now", "2026-09-30T10:00:00+00:00"))),
                messages=tuple(str(m) for m in raw.get("messages", [])),
                digest=raw.get("digest"),
                model=raw.get("model") or {},
                question=raw.get("question"),
                expect=raw.get("expect") or {},
                path=path,
            )
        )
    return cases


def link_router(case: LinkCase, resources: Path = DEFAULT_RESOURCES_DIR) -> ModelRouter:
    """The fake provider replaying the case: its digest for every page, and its recorded
    outputs for the message that isn't only a link (or the question)."""
    replay: dict[str, dict[str, Any]] = {}
    if case.model:
        last = case.question or without_links(case.messages[-1], extract_urls(case.messages[-1]))
        replay[replay_key(last)] = dict(case.model)
    responders = (
        offline_responders(replay) | recall_responders(replay) | correction_responders(replay)
    )
    if case.digest is not None:
        recorded = dict(case.digest)

        def digest(request: AdapterRequest) -> dict[str, Any]:
            return dict(recorded)

        responders = responders | {"digest": digest}
    script = FakeScript(responders=responders, text_responders=recall_text_responders())
    routing = resolve_routing(read_routing_file(resources / "config" / "models.yaml"), {}, "fake")
    return ModelRouter(
        routing=routing,
        prices=read_price_table(resources / "config" / "prices.yaml"),
        adapters={"fake": FakeProvider("fake", script=script)},
        policy=ResiliencePolicy(max_retries=0),
    )


# ------------------------------------------------------------------ running


@dataclass(slots=True)
class LinkResult:
    case: LinkCase
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


async def run_case(
    db: Database,
    scope: WorkspaceScope,
    case: LinkCase,
    *,
    transport: FixtureTransport | None = None,
) -> LinkResult:
    transport = transport or FixtureTransport()

    def stores(s: WorkspaceScope) -> SqlLinkStore:
        return SqlLinkStore(db, s)

    runner = eval_runner(
        db,
        link_router(case),
        now=case.now,
        link_saver=LinkSaver(stores, Memory(sql_memory(db)), SaveSettings()),
        link_reading=LinkReading(fetcher=transport, stores=stores, settings=READ),
    )
    run = _Run(db, scope, runner, transport, LinkResult(case))
    try:
        await (run.ingest() if case.suite == "ingest" else run.recall())
        if transport.unknown:
            run.fail(f"requested pages the fixture doesn't have: {transport.unknown}")
    finally:
        await runner.aclose()
    return run.result


@dataclass(slots=True)
class _Run:
    db: Database
    scope: WorkspaceScope
    runner: TurnRunner
    transport: FixtureTransport
    result: LinkResult

    @property
    def case(self) -> LinkCase:
        return self.result.case

    def fail(self, message: str) -> None:
        self.result.failures.append(message)

    async def say(self, message: str) -> Turn:
        """One chat turn, then what the worker does for each link it saved."""
        handle = await self.runner.start(self.scope, text=message, timezone="UTC")
        async for _ in handle.events():
            pass
        turn = await SqlTurnStore(self.db, self.scope).get(handle.turn.id)
        if turn is None:
            raise RuntimeError("the turn is gone")
        if turn.status.value == "completed":
            for event in await self.events(turn):
                if isinstance(event, FetchEvent) and event.status == "pending":
                    await self.runner.read_link(self.scope, event.item_id, timezone="UTC")
        return turn

    async def events(self, turn: Turn | uuid.UUID) -> list[Any]:
        turn_id = turn if isinstance(turn, uuid.UUID) else turn.id
        return [e.event for e in await SqlTurnStore(self.db, self.scope).events(turn_id)]

    # ------------------------------------------------------------------ ingest

    async def ingest(self) -> None:
        want = self.case.expect
        turn = None
        for message in self.case.messages:
            turn = await self.say(message)
            if turn.status.value != "completed":
                self.fail(f"a turn ended {turn.status.value}: {turn.error_message}")
                return
        if turn is None:
            return
        reply = turn.output or ""
        for needle in want.get("reply_contains", []):
            if needle not in reply:
                self.fail(f"the reply lacks {needle!r}: {reply!r}")
        links = SqlLinkStore(self.db, self.scope)
        sources = await links.count_sources()
        if "sources" in want and sources != want["sources"]:
            self.fail(f"{sources} link sources; expected {want['sources']}")
        asked = self.transport.requests
        if "requests" in want and len(asked) != want["requests"]:
            self.fail(f"{len(asked)} requests {asked}; expected {want['requests']}")
        if "url" not in want:
            return
        source = await links.by_canonical(canonical_url(want["url"]))
        if source is None:
            self.fail(f"no link source for {want['url']}")
            return
        await self.check_source(links, source)
        if "items" in want:
            await self.check_items(turn.id, want["items"])

    async def check_source(self, links: SqlLinkStore, source: LinkSource) -> None:
        want = self.case.expect

        def check(name: str, got: object) -> None:
            if name in want and got != want[name]:
                self.fail(f"{name}: {got!r}; expected {want[name]!r}")

        check("status", source.fetch_status.value)
        check("reason", source.fetch_reason)
        check("kind", source.kind.value)
        check("site", source.site)
        check("author", source.author)
        check("channel", source.channel)
        check("duration_s", source.duration_s)
        check("final_host", source.final_host)
        check("redirects", source.redirects)
        check("extraction_method", source.extraction_method)
        chunks = await links.chunks(source.item_id)
        if "chunks_min" in want and len(chunks) < want["chunks_min"]:
            self.fail(f"{len(chunks)} chunks; expected at least {want['chunks_min']}")
        if "chunks" in want and len(chunks) != want["chunks"]:
            self.fail(f"{len(chunks)} chunks; expected {want['chunks']}")
        joined = " ".join(c.text for c in chunks).lower()
        for needle in want.get("chunks_contain", []):
            if needle.lower() not in joined:
                self.fail(f"no chunk holds {needle!r}")
        await self.check_item(source)
        fetches = [
            e
            for e in await self.events(source.turn_id)
            if isinstance(e, FetchEvent) and e.item_id == source.item_id
        ]
        if not fetches:
            self.fail("no fetch event on the turn that saved the link")
        for needle in want.get("message_contains", []):
            if fetches and needle not in fetches[-1].message:
                self.fail(f"the Trail says {fetches[-1].message!r}; expected {needle!r}")

    async def check_item(self, source: LinkSource) -> None:
        want = self.case.expect
        item = await self.runner.memory.reader(self.scope).item(source.item_id)
        if item is None or item.status is not ItemStatus.ACTIVE:
            self.fail("the link's item is missing")
            return
        if "title" in want and item.title != want["title"]:
            self.fail(f"title: {item.title!r}; expected {want['title']!r}")
        for needle in want.get("summary_contains", []):
            if needle.lower() not in (item.summary or "").lower():
                self.fail(f"the summary lacks {needle!r}: {item.summary!r}")
        if "tags" in want and sorted(item.tags) != sorted(want["tags"]):
            self.fail(f"tags: {sorted(item.tags)}; expected {sorted(want['tags'])}")
        if "said" in want:
            if want["said"] not in item.text:
                self.fail(f"what the person said isn't kept: {item.text!r}")
            if item.trust.value != "user_stated":
                self.fail(f"trust is {item.trust.value}; the person's words stay theirs")

    async def check_items(self, turn_id: uuid.UUID, counts: Mapping[str, int]) -> None:
        reader = self.runner.memory.reader(self.scope)
        rows = await reader.write_log(turn_id)
        ids = list(dict.fromkeys(r.target_id for r in rows if r.target_type.value == "item"))
        found: dict[str, int] = {}
        for item in await reader.items(ids):
            found[item.kind.value] = found.get(item.kind.value, 0) + 1
        if found != dict(counts):
            self.fail(f"the turn wrote {found}; expected {dict(counts)}")

    # ------------------------------------------------------------------ recall

    async def recall(self) -> None:
        want = self.case.expect
        for message in self.case.messages:
            await self.say(message)
        if self.case.question is None:
            raise RuntimeError(f"{self.case.id} has no question")
        turn = await self.say(self.case.question)
        if turn.status.value != "completed":
            self.fail(f"the question ended {turn.status.value}: {turn.error_message}")
            return
        events = await self.events(turn)
        retrieval = next((e for e in events if isinstance(e, RetrievalEvent)), None)
        citations = next((e for e in events if isinstance(e, CitationsEvent)), None)
        if retrieval is None or citations is None:
            self.fail("the turn has no retrieval or citations event")
            return
        reader = self.runner.memory.reader(self.scope)
        cited = [
            (c, await reader.item(c.item_id) if c.item_id else None) for c in citations.citations
        ]
        urls = sorted({i.content_ref for _, i in cited if i is not None and i.content_ref})
        if "cites" in want and urls != sorted(want["cites"]):
            self.fail(f"cited {urls}; expected {sorted(want['cites'])}")
        distinct = len({c.item_id for c, _ in cited})
        if want.get("one_item") and distinct != 1:
            self.fail(f"{distinct} items cited; expected one")
        snippets = " ".join(c.snippet or "" for c, _ in cited).lower()
        for needle in want.get("snippet_contains", []):
            if needle.lower() not in snippets:
                self.fail(f"no cited passage holds {needle!r}: {snippets!r}")
        if "from_page" in want and {c.from_page for c, _ in cited} != {want["from_page"]}:
            self.fail(f"a citation's from_page differs from {want['from_page']}")
        matched = [
            c.matched_chunk
            for q in retrieval.sub_queries
            for c in q.candidates
            if c.matched_chunk is not None
        ]
        if "matched_chunk" in want and bool(matched) != want["matched_chunk"]:
            self.fail(f"a chunk matched: {bool(matched)}; expected {want['matched_chunk']}")
        deep = want.get("deep_chunk")
        if deep is not None and not any(position >= deep for position in matched):
            self.fail(f"no passage from position {deep} or later matched (saw {matched})")
        for needle in want.get("reply_contains", []):
            if needle.lower() not in (turn.output or "").lower():
                self.fail(f"the reply lacks {needle!r}: {turn.output!r}")


def summary(results: Sequence[LinkResult]) -> str:
    return "\n".join(
        f"{'ok  ' if r.passed else 'FAIL'} {r.case.id}  {r.case.title}"
        + "".join(f"\n       {f}" for f in r.failures)
        for r in results
    )


__all__ = [
    "FixtureTransport",
    "LinkCase",
    "LinkResult",
    "load_cases",
    "run_case",
    "summary",
]
