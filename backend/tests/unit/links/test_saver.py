"""S4.7: the links in a message become resource items at once, and the turn says the page is being
read. Limits, refusals and duplicates are saved or recognised without a request."""

import uuid

import pytest

from secondmind.core import FetchEvent, Kind, NullTrail, ResourceFormat, Source, Trust
from secondmind.links import FetchStatus, LinkKind, LinkSaver, LinkSource, SaveSettings
from tests.unit.memory.helpers import NOW, memory, scope, turn


class Store:
    def __init__(self) -> None:
        self.sources: dict[uuid.UUID, LinkSource] = {}

    async def add(self, source: LinkSource) -> None:
        self.sources[source.item_id] = source

    async def by_canonical(self, canonical_url: str) -> LinkSource | None:
        return next((s for s in self.sources.values() if s.canonical_url == canonical_url), None)


async def save(
    message: str, *, max_links: int = 3, rate: float | None = None, store: Store | None = None
):  # type: ignore[no-untyped-def]
    events: list[object] = []

    async def sink(event: object) -> None:
        events.append(event)

    async def rate_check() -> float | None:
        return rate

    mem, _ = memory()
    ws = scope()
    t = turn(ws)
    store = store or Store()
    saver = LinkSaver(store, mem, SaveSettings(max_links=max_links), rate_check)
    result = await saver.save(
        scope=ws,
        turn_id=t.turn_id,
        message=message,
        now=NOW,
        trail=NullTrail(sink),  # type: ignore[arg-type]
    )
    return result, store, events, mem, ws


async def test_a_message_without_a_link_is_not_the_savers_business() -> None:
    result, store, events, *_ = await save("I like oolong tea")
    assert result is None
    assert store.sources == {}
    assert events == []


async def test_a_link_becomes_a_resource_item_and_a_pending_source() -> None:
    result, store, events, mem, ws = await save(
        "Read this later https://journal.example/sleep-tips for the sleep tips"
    )
    assert result is not None
    assert result.reply == "Saved the link. Reading it now."
    assert result.remaining == "Read this later [link] for the sleep tips"
    (link,) = result.links
    assert link.status is FetchStatus.PENDING
    source = store.sources[link.item_id]
    assert source.fetch_status is FetchStatus.PENDING
    assert source.kind is LinkKind.LINK
    assert source.canonical_url == "https://journal.example/sleep-tips"
    item = await mem.reader(ws).item(link.item_id)
    assert item is not None
    assert item.kind is Kind.RESOURCE
    assert item.format is ResourceFormat.LINK
    assert item.source is Source.LINK
    assert item.trust is Trust.USER_STATED  # what the person said, as they said it
    assert item.text == "Read this later [link] for the sleep tips"
    assert item.title == "Link: journal.example"
    assert item.content_ref == "https://journal.example/sleep-tips"
    (fetch,) = [e for e in events if isinstance(e, FetchEvent)]
    assert fetch.status == "pending"
    assert fetch.host == "journal.example"
    assert "journal.example/sleep" not in fetch.model_dump_json()  # the host, never the path


async def test_a_bare_link_is_saved_with_a_plain_description() -> None:
    result, _, _, mem, ws = await save("https://www.youtube.com/watch?v=abc123")
    assert result is not None
    assert result.only_links
    item = await mem.reader(ws).item(result.links[0].item_id)
    assert item is not None
    assert item.format is ResourceFormat.VIDEO
    assert item.text == "A link to youtube.com"


async def test_a_link_mixed_with_other_things_leaves_the_rest_to_be_saved_as_usual() -> None:
    result, *_ = await save("Add a task: call Nisha tomorrow. Also https://a.example/x is good")
    assert result is not None
    assert not result.only_links
    assert result.remaining == "Add a task: call Nisha tomorrow. Also [link] is good"


async def test_several_links_are_saved_up_to_the_limit_and_the_rest_are_refused() -> None:
    message = " ".join(f"https://site{i}.example/post" for i in range(5))
    result, store, *_ = await save(message, max_links=3)
    assert result is not None
    statuses = [link.status for link in result.links]
    assert statuses == [FetchStatus.PENDING] * 3 + [FetchStatus.REFUSED] * 2
    assert result.links[3].rule == "too_many_links"
    assert "Saved 3 links. Reading them now." in result.reply
    assert "too many links in one message" in result.reply
    assert len(store.sources) == 5  # refused links are saved too, with the reason
    assert {s.fetch_status for s in store.sources.values()} == {
        FetchStatus.PENDING,
        FetchStatus.REFUSED,
    }


@pytest.mark.parametrize(
    ("url", "rule", "label"),
    [
        ("http://169.254.169.254/latest/meta-data/", "link_local", "link-local address"),
        ("http://localhost/admin", "loopback", "this server's own address"),
        ("https://example.com:8080/", "port", "unusual port"),
        ("https://10.0.0.5/", "private_address", "private address"),
        ("ftp://files.example/x", "scheme", "not a web link"),
        ("https://user:pw@example.com/", "credentials", "it has a password in it"),
    ],
)
async def test_a_link_a_safety_rule_refuses_is_saved_as_refused_with_no_request(
    url: str, rule: str, label: str
) -> None:
    result, store, events, *_ = await save(f"look at {url}")
    assert result is not None
    (link,) = result.links
    assert (link.status, link.rule, link.reason) == (FetchStatus.REFUSED, rule, label)
    assert result.reply == f"Saved the link, but didn't open it: {label}."
    source = store.sources[link.item_id]
    assert source.fetch_status is FetchStatus.REFUSED
    assert source.fetch_reason == label
    assert source.detail == {"rule": rule}
    (fetch,) = [e for e in events if isinstance(e, FetchEvent)]
    assert (fetch.status, fetch.rule) == ("refused", rule)
    assert fetch.message == f"Didn't open it: {label}."


async def test_over_the_fetch_rate_limit_a_link_is_refused_not_dropped() -> None:
    result, store, *_ = await save("https://a.example/x", rate=12.0)
    assert result is not None
    assert result.links[0].rule == "rate_limited"
    assert len(store.sources) == 1


async def test_the_same_link_twice_is_the_item_already_there() -> None:
    first, store, *_ = await save("https://journal.example/post?utm_source=mail")
    assert first is not None
    again, *_ = await save("saving it again https://www.journal.example/post#top", store=store)
    assert again is not None
    (link,) = again.links
    assert link.duplicate_of == first.links[0].item_id
    assert again.reply == "You already saved that link."
    assert len(store.sources) == 1  # no second item


async def test_the_same_link_twice_in_one_message_is_saved_once() -> None:
    result, store, *_ = await save("https://a.example/x and again https://a.example/x/")
    assert result is not None
    assert len(store.sources) == 1
