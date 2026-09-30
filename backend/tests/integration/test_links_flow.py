"""S4.7 end to end on Postgres, with the fake provider and a scripted fetcher: a message with a
link saves a pending resource item and says it is reading; the worker's read fills the item in,
stores chunks, appends a `fetch` event and puts its cost on the saving turn; undoing the turn
removes it all. A refused link is saved as refused with no request."""

from typing import Any

import pytest
from sqlalchemy import text

from secondmind.agent import Turn, TurnRunner
from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import FetchEvent, ItemStatus, TargetType, WorkspaceScope
from secondmind.evals.recall import eval_runner, fake_router, load_cases
from secondmind.links import (
    FetchedPage,
    FetchStatus,
    LinkReading,
    LinkSaver,
    ReadSettings,
    SaveSettings,
)
from secondmind.links.adapters import SqlLinkStore
from secondmind.memory import Memory
from secondmind.memory.adapters import Database, sql_memory
from secondmind.retrieval import Access, Filters, Query
from secondmind.retrieval.adapters import SqlRecallStore
from tests.integration.memory_seed import workspace
from tests.unit.links.test_reader import PROSE, html

pytestmark = pytest.mark.integration

NOW = None


class Fetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.urls: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.urls.append(url)
        return FetchedPage(
            url=url,
            final_url=url,
            final_host="journal.example",
            status_code=200,
            content_type="text/html",
            charset=None,
            body=self.body,
            redirects=0,
            hosts=("journal.example",),
        )


def runner_for(db: Database, fetcher: Fetcher, router: Any = None) -> tuple[TurnRunner, Any]:
    from datetime import UTC, datetime  # noqa: PLC0415

    stores = lambda scope: SqlLinkStore(db, scope)  # noqa: E731
    memory = Memory(sql_memory(db))
    runner = eval_runner(
        db,
        router or fake_router(load_cases()),
        now=datetime(2026, 9, 30, 10, 0, tzinfo=UTC),
        link_saver=LinkSaver(stores, memory, SaveSettings()),
        link_reading=LinkReading(
            fetcher=fetcher,  # type: ignore[arg-type]
            stores=stores,
            settings=ReadSettings(chunk_tokens=120, max_chunks=10),
        ),
    )
    return runner, stores


async def say(db: Database, runner: TurnRunner, scope: WorkspaceScope, message: str) -> Turn:
    handle = await runner.start(scope, text=message, timezone="UTC")
    async for _ in handle.events():
        pass
    turn = await SqlTurnStore(db, scope).get(handle.turn.id)
    assert turn is not None
    return turn


async def fetch_events(db: Database, scope: WorkspaceScope, turn: Turn) -> list[FetchEvent]:
    return [
        e.event
        for e in await SqlTurnStore(db, scope).events(turn.id)
        if isinstance(e.event, FetchEvent)
    ]


async def test_a_saved_link_is_read_by_the_worker_and_undone_with_its_turn(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    scope = await workspace(identity)
    body = html(
        f"<article><h1>Sleep well</h1><p>{PROSE}</p></article>",
        "<meta property='og:site_name' content='The Journal'>",
    )
    fetcher = Fetcher(body)
    runner, stores = runner_for(app_db, fetcher)
    links = stores(scope)

    turn = await say(
        app_db, runner, scope, "Saved for later https://journal.example/sleep for the sleep tips"
    )
    assert turn.status.value == "completed", turn.error_message
    assert (turn.output or "").strip().endswith("Saved the link. Reading it now.")
    assert fetcher.urls == []  # the turn never waited for the page
    (pending,) = await fetch_events(app_db, scope, turn)
    assert (pending.status, pending.host) == ("pending", "journal.example")
    source = await links.by_item(pending.item_id)
    assert source is not None
    assert source.fetch_status is FetchStatus.PENDING

    result = await runner.read_link(scope, pending.item_id, timezone="UTC")
    assert result is not None
    assert result.status is FetchStatus.FULL
    assert fetcher.urls == ["https://journal.example/sleep"]

    source = await links.by_item(pending.item_id)
    assert source is not None
    assert source.fetch_status is FetchStatus.FULL
    assert source.site == "The Journal"
    assert source.final_host == "journal.example"
    assert source.word_count is not None
    assert source.word_count > 100
    assert source.chunk_count == len(await links.chunks(pending.item_id)) >= 2
    item = await runner.memory.reader(scope).item(pending.item_id)
    assert item is not None
    assert item.title == "Sleep well"  # the digest's title, written from the page
    assert item.summary
    assert "sleep tips" in item.text  # what the person said is still there, as they said it
    assert item.trust.value == "user_stated"

    # The read is on the original turn: its write log, its Trail and its ledger.
    rows = await runner.memory.reader(scope).write_log(turn.id)
    assert any(r.target_id == pending.item_id and r.op.value == "update" for r in rows)
    done = [e for e in await fetch_events(app_db, scope, turn) if e.status == "full"]
    assert len(done) == 1
    assert done[0].message.startswith("Read Sleep well · The Journal · ")
    assert done[0].chunks == source.chunk_count
    async with app_db.workspace(scope) as session:
        steps = {
            r[0]: r[1]
            for r in await session.execute(
                text(
                    "SELECT step, bool_or(system) FROM usage_ledger "
                    "WHERE turn_id = :t GROUP BY step"
                ),
                {"t": turn.id},
            )
        }
    assert {"digest", "embed"} <= set(steps)
    assert steps["digest"] is False  # the person's cost: it is their save

    # Nothing left to do for a link that has been read.
    assert await runner.read_link(scope, pending.item_id, timezone="UTC") is None

    # Undoing the turn that saved the link removes the item, and the page is never read again.
    await runner.undo(scope, turn_id=turn.id, timezone="UTC")
    after = await runner.memory.reader(scope).item(pending.item_id)
    assert after is not None
    assert after.status is ItemStatus.DELETED
    assert await links.by_canonical("https://journal.example/sleep") is None
    await runner.aclose()


async def test_a_refused_link_is_saved_as_refused_and_nothing_is_requested(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    scope = await workspace(identity)
    fetcher = Fetcher(b"")
    runner, stores = runner_for(app_db, fetcher)
    turn = await say(app_db, runner, scope, "look at http://169.254.169.254/latest/meta-data/")
    (event,) = await fetch_events(app_db, scope, turn)
    assert (event.status, event.rule) == ("refused", "link_local")
    assert "link-local address" in (turn.output or "")
    source = await stores(scope).by_item(event.item_id)
    assert source is not None
    assert source.fetch_status is FetchStatus.REFUSED
    assert await runner.read_link(scope, event.item_id, timezone="UTC") is None  # nothing to read
    assert fetcher.urls == []
    await runner.aclose()


async def test_the_same_link_twice_is_the_item_already_saved(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    scope = await workspace(identity)
    runner, stores = runner_for(app_db, Fetcher(html(f"<article><p>{PROSE}</p></article>")))
    first = await say(app_db, runner, scope, "https://journal.example/sleep?utm_source=mail")
    again = await say(app_db, runner, scope, "that one again https://www.journal.example/sleep#x")
    assert "You already saved that link." in (again.output or "")
    assert await fetch_events(app_db, scope, again) == []
    assert await stores(scope).count_sources() == 1
    assert len(await fetch_events(app_db, scope, first)) == 1
    await runner.aclose()


async def test_a_message_with_a_link_and_more_saves_the_rest_as_usual(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    scope = await workspace(identity)
    runner, _ = runner_for(app_db, Fetcher(b""))
    turn = await say(
        app_db, runner, scope, "I like oolong tea, and save https://tea.example/guide for later"
    )
    assert turn.status.value == "completed", turn.error_message
    assert "Saved the link. Reading it now." in (turn.output or "")
    rows = await runner.memory.reader(scope).write_log(turn.id)
    ids = list(dict.fromkeys(r.target_id for r in rows if r.target_type is TargetType.ITEM))
    items = await runner.memory.reader(scope).items(ids)
    assert len(items) >= 2  # the link's item, and what the rest of the message said
    assert any("oolong" in i.text for i in items)
    await runner.aclose()


async def test_recall_finds_what_only_the_page_body_says_and_hands_over_the_passage(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    from secondmind.core import KeyKind  # noqa: PLC0415

    scope = await workspace(identity)
    body = html(
        "<article><h1>Gardening by the moon</h1>"
        f"<p>{PROSE}</p>"
        "<p>Gardeners have long planted carrots in the dark of the moon, and the old almanacs "
        "insist that lettuce sown under a waxing crescent bolts more slowly than lettuce sown at "
        "any other time of the month. There is, frankly, no evidence for it at all.</p></article>"
    )
    runner, _ = runner_for(app_db, Fetcher(body))
    turn = await say(app_db, runner, scope, "https://garden.example/moon for the garden")
    (pending,) = await fetch_events(app_db, scope, turn)
    await runner.read_link(scope, pending.item_id, timezone="UTC")

    store = SqlRecallStore(app_db, scope, timeout_ms=10_000)
    # Nothing the person said mentions lettuce or crescent moons: only the page body does.
    hits = await store.search(
        Query("lettuce waxing crescent bolts", None, "fake"), Filters(), Access(), limit=10
    )
    (hit,) = [h for h in hits if h.item_id == pending.item_id]
    assert hit.matched_key is KeyKind.CHUNK
    assert hit.snippet is not None
    assert "lettuce" in hit.snippet
    assert hit.snippet_position is not None
    # The item found is the link's own, so a citation points at the item.
    item = await runner.memory.reader(scope).item(hit.item_id)
    assert item is not None
    assert item.content_ref == "https://garden.example/moon"
    await runner.aclose()


async def test_passages_saved_without_vectors_get_them_later_and_are_found_by_words_until_then(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    from secondmind.providers import ProviderErrorKind  # noqa: PLC0415

    scope = await workspace(identity)
    router = fake_router(load_cases())
    provider = router._adapters["fake"]
    provider.embed_error = ProviderErrorKind.CONNECTION
    body = html(f"<article><h1>Sleep well</h1><p>{PROSE}</p></article>")
    runner, stores = runner_for(app_db, Fetcher(body), router)
    links = stores(scope)
    turn = await say(app_db, runner, scope, "https://journal.example/sleep for later")
    (pending,) = await fetch_events(app_db, scope, turn)
    result = await runner.read_link(scope, pending.item_id, timezone="UTC")
    assert result is not None
    assert result.status is FetchStatus.FULL  # the page is read and saved even with embeddings down
    chunks = await links.chunks(pending.item_id)
    assert chunks
    assert all(c.embedding is None for c in chunks)
    assert len(await links.unembedded_chunks()) == len(chunks)

    # Still down: nothing is embedded, nothing is lost.
    assert await runner.embed_pending_chunks(scope) == 0
    assert len(await links.unembedded_chunks()) == len(chunks)

    # The provider answers again: the periodic job gives every passage its vector.
    provider.embed_error = None
    assert await runner.embed_pending_chunks(scope) == len(chunks)
    assert await links.unembedded_chunks() == []
    embedded = await links.chunks(pending.item_id)
    assert all(c.embedding is not None and c.embedding_model for c in embedded)
    await runner.aclose()


async def test_text_added_to_a_partly_read_link_completes_it_and_counts_as_content(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    scope = await workspace(identity)
    walled = html(
        "<article><h1>Big story</h1><p>The first paragraph is free to read, and says the council "
        "voted on Tuesday to approve the new plan after a long debate about its cost.</p>"
        "<div class='paywall'>Subscribe to continue reading.</div></article>"
    )
    runner, stores = runner_for(app_db, Fetcher(walled))
    links = stores(scope)
    turn = await say(app_db, runner, scope, "https://news.example/big-story for the council")
    (pending,) = await fetch_events(app_db, scope, turn)
    first = await runner.read_link(scope, pending.item_id, timezone="UTC")
    assert first is not None
    assert (first.status, first.reason) == (FetchStatus.PARTIAL, "paywall")
    assert first.message == "Could only read part of it (paywall). Add the text?"
    partial = await links.by_item(pending.item_id)
    assert partial is not None
    assert partial.fetch_status is FetchStatus.PARTIAL

    # "Add the text": pasted text goes through the same digest and chunking, as content.
    pasted = PROSE + " The council's plan also moves the library to the old mill."
    done = await runner.read_link(scope, pending.item_id, timezone="UTC", pasted=pasted)
    assert done is not None
    assert done.status is FetchStatus.FULL
    assert done.extraction_method == "pasted"
    source = await links.by_item(pending.item_id)
    assert source is not None
    assert source.fetch_status is FetchStatus.FULL
    assert any("old mill" in c.text for c in await links.chunks(pending.item_id))
    item = await runner.memory.reader(scope).item(pending.item_id)
    assert item is not None
    assert item.trust.value == "user_stated"  # the person's own words stay theirs
    assert "council" in item.text
    # A link that has been read in full takes no more text by this route's own rule.
    full = [e for e in await fetch_events(app_db, scope, turn) if e.status == "full"]
    assert full
    await runner.aclose()
