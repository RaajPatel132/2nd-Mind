"""S4.7: saved links in Postgres. A link's source row, its chunk keys, the same link found again by
its canonical address, and isolation between workspaces (the catalog test covers RLS on the table;
this checks the repository behaves)."""

import hashlib

import pytest

from secondmind.auth.adapters import SqlIdentityStore
from secondmind.core import KeyKind, Kind, new_id
from secondmind.links.adapters import SqlLinkStore
from secondmind.links.model import ChunkRow, FetchStatus, LinkKind, LinkSource
from secondmind.memory import DeleteItem, Memory
from secondmind.memory.adapters import Database, sql_memory
from tests.integration.memory_seed import EMBED_MODEL, real_turn, vector, workspace
from tests.unit.memory.helpers import create, item

pytestmark = pytest.mark.integration


async def _saved_link(
    db: Database, identity: SqlIdentityStore, url: str = "https://a.example/post"
):  # type: ignore[no-untyped-def]
    scope = await workspace(identity)
    turn = await real_turn(db, scope)
    memory = Memory(sql_memory(db))
    op = create(item("Saved the link about sleep", Kind.RESOURCE))
    writer = memory.writer(scope, turn)
    writer.add(op)
    await writer.commit()
    store = SqlLinkStore(db, scope)
    source = LinkSource(
        id=new_id(),
        workspace_id=scope.workspace_id,
        item_id=op.item_id,
        turn_id=turn.turn_id,
        url=url,
        canonical_url=url,
        kind=LinkKind.ARTICLE,
    )
    await store.add(source)
    return scope, turn, op.item_id, store, source


async def test_a_link_source_is_stored_and_updated_as_the_read_goes(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    _, _, item_id, store, source = await _saved_link(app_db, identity)
    found = await store.by_item(item_id)
    assert found is not None
    assert found.fetch_status is FetchStatus.PENDING
    assert found.url == source.url
    assert found.created_at is not None
    await store.update(
        item_id,
        fetch_status=FetchStatus.FULL,
        site="a.example",
        word_count=1234,
        final_host="a.example",
        status_code=200,
        detail={"note": "ok"},
    )
    after = await store.by_item(item_id)
    assert after is not None
    assert after.fetch_status is FetchStatus.FULL
    assert after.word_count == 1234
    assert after.detail == {"note": "ok"}
    with pytest.raises(ValueError, match="not a link source field"):
        await store.update(item_id, workspace_id=new_id())


async def test_chunks_are_keys_of_the_item_and_replace_each_other(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    scope, _, item_id, store, _ = await _saved_link(app_db, identity)
    texts = ["First passage about sleep.", "Second passage about light.", "Third passage."]

    def rows(pieces: list[str], embed: bool = True) -> list[ChunkRow]:
        return [
            ChunkRow(
                id=new_id(),
                position=i,
                text=t,
                content_hash=hashlib.sha256(t.encode()).hexdigest(),
                embedding=vector(t) if embed else None,
                embedding_model=EMBED_MODEL if embed else None,
            )
            for i, t in enumerate(pieces)
        ]

    await store.replace_chunks(item_id, rows(texts))
    chunks = await store.chunks(item_id)
    assert [c.text for c in chunks] == texts
    assert [c.position for c in chunks] == [0, 1, 2]
    source = await store.by_item(item_id)
    assert source is not None
    assert source.chunk_count == 3
    # They are memory keys of kind chunk, next to the item's own keys.
    memory = Memory(sql_memory(app_db))
    keys = await memory.reader(scope).keys([item_id])
    assert [k.key_kind for k in keys if k.key_kind is KeyKind.CHUNK] == [KeyKind.CHUNK] * 3
    # A second read replaces the first.
    await store.replace_chunks(item_id, rows(["Only one now."], embed=False))
    assert [c.text for c in await store.chunks(item_id)] == ["Only one now."]
    assert await store.unembedded_chunks() != []  # waiting for its vector (ledger 47)
    source = await store.by_item(item_id)
    assert source is not None
    assert source.chunk_count == 1


async def test_the_same_link_is_found_again_by_its_canonical_address_while_its_item_lives(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    scope, _, item_id, store, source = await _saved_link(app_db, identity)
    again = await store.by_canonical(source.canonical_url)
    assert again is not None
    assert again.item_id == item_id
    assert await store.by_canonical("https://nowhere.example/") is None
    # Delete the item (an undo does this): the address is free again.
    memory = Memory(sql_memory(app_db))
    writer = memory.writer(scope, await real_turn(app_db, scope), confirmed=True)
    writer.add(DeleteItem(item_id=item_id, title="undo"))
    result = await writer.commit()
    assert item_id in result.touched_items
    assert await store.by_canonical(source.canonical_url) is None


async def test_one_workspace_cannot_see_anothers_links_or_chunks(
    app_db: Database, identity: SqlIdentityStore
) -> None:
    _, _, item_a, store_a, source = await _saved_link(app_db, identity)
    scope_b = await workspace(identity)
    store_b = SqlLinkStore(app_db, scope_b)
    assert await store_b.by_item(item_a) is None
    assert await store_b.by_canonical(source.canonical_url) is None
    assert await store_b.by_items([item_a]) == {}
    assert await store_b.count_sources() == 0
    assert await store_a.count_sources() == 1
