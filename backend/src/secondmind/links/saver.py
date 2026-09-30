"""Saving the links in a message (S4.7): each URL becomes one ``resource`` item that holds what the
person said, plus a ``link_sources`` row that says where it came from. The page is read later, by
the worker (``fetch_link``): the turn completes at once with "Saved the link. Reading it now."

A link over the limits, or one a safety rule refuses, is still saved, as ``refused`` with the
reason and no request. The same link twice (by canonical address) is the item that's already there.
"""

import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from secondmind.core import (
    AgentStep,
    FetchEvent,
    Kind,
    PolicyDecision,
    PolicyVerdict,
    ResourceFormat,
    Source,
    StepStatus,
    ToolCallEvent,
    Trail,
    Trust,
    WorkspaceScope,
    initial_state,
    new_id,
)
from secondmind.links.model import PENDING_REPLY, FetchStatus, LinkKind, LinkSource
from secondmind.links.reader import RULE_LABELS
from secondmind.links.urls import (
    FetchRefusedError,
    canonical_url,
    extract_urls,
    host_for_log,
    parse_url,
    site_name,
    video_site,
)
from secondmind.memory import CreateItem, ItemContent, Memory, WriterTurn


class LinkStore(Protocol):
    async def add(self, source: LinkSource) -> None: ...

    async def by_canonical(self, canonical_url: str) -> LinkSource | None: ...


# Seconds to wait, or None when this person may fetch another link now.
RateCheck = Callable[[WorkspaceScope], Awaitable[float | None]]
LinkStores = Callable[[WorkspaceScope], LinkStore]


@dataclass(frozen=True, slots=True)
class SaveSettings:
    max_links: int = 3
    allow_hosts: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SavedLink:
    item_id: uuid.UUID
    url: str
    status: FetchStatus  # pending, or refused
    host: str
    reason: str | None = None
    rule: str | None = None
    duplicate_of: uuid.UUID | None = None


@dataclass(slots=True)
class LinkSaveResult:
    remaining: (
        str  # the message with each link replaced by "[link]": what the rest of the save sees
    )
    links: list[SavedLink] = field(default_factory=list)
    reply: str = ""

    @property
    def pending(self) -> list[SavedLink]:
        return [link for link in self.links if link.status is FetchStatus.PENDING]

    @property
    def only_links(self) -> bool:
        """Nothing is left to save once the links are taken out (a word or two of framing)."""
        words = re.sub(r"\[link\]", " ", self.remaining)
        return len(re.findall(r"[\w']+", words)) <= 4


def fetch_tool_call(
    *, host: str, rule: str | None, reason: str | None, refused: bool, summary: str = ""
) -> ToolCallEvent:
    """The Tool calls panel's row for one request: ``web.fetch(host)``, allowed or refused, and
    the rule that refused it (FR-4.6). Only the host is recorded, never the path or query."""
    if refused:
        verdict = PolicyVerdict(
            decision=PolicyDecision.BLOCKED,
            rule_id=rule or "FETCH-REFUSED",
            reason=f"Didn't open it: {reason or 'not allowed'}.",
        )
        summary = summary or "refused, no request was made"
    else:
        verdict = PolicyVerdict(
            decision=PolicyDecision.ALLOWED,
            rule_id="FETCH-SAFE",
            reason="A public address: one request, no cookies, nothing in the page followed.",
        )
    return ToolCallEvent(
        tool="web.fetch",
        arguments={"host": host},
        result_summary=summary,
        policy=verdict,
        access="read",
    )


def without_links(message: str, urls: Sequence[str]) -> str:
    text = message
    for url in urls:
        text = text.replace(url, "[link]")
    return re.sub(r"\s+", " ", text).strip()


class LinkSaver:
    def __init__(
        self,
        stores: LinkStores,
        memory: Memory,
        settings: SaveSettings | None = None,
        rate_check: RateCheck | None = None,
    ) -> None:
        self._stores = stores
        self._memory = memory
        self._settings = settings or SaveSettings()
        self._rate = rate_check

    async def save(
        self,
        *,
        scope: WorkspaceScope,
        turn_id: uuid.UUID,
        message: str,
        now: datetime,
        trail: Trail,
    ) -> LinkSaveResult | None:
        urls = extract_urls(message)
        if not urls:
            return None
        store = self._stores(scope)
        said = without_links(message, urls)
        result = LinkSaveResult(remaining=said)
        ops: list[CreateItem] = []
        sources: list[LinkSource] = []
        seen: set[str] = set()
        for index, url in enumerate(urls):
            link = await self._judge(store, scope, url, index, seen)
            if link.duplicate_of is None:
                item, source = self._item(
                    scope=scope, turn_id=turn_id, url=url, said=said, now=now, link=link
                )
                ops.append(item)
                sources.append(source)
                link = SavedLink(item.item_id, url, link.status, link.host, link.reason, link.rule)
            result.links.append(link)
        if ops:
            writer = self._memory.writer(
                scope,
                WriterTurn(turn_id=turn_id, workspace_id=scope.workspace_id, kind="user", now=now),
                emit=trail.emit,  # the glass box shows the save, and the turn can be undone from it
            )
            writer.add(*ops)
            await writer.commit()
            for source in sources:
                await store.add(source)
        await self._report(trail, result)
        result.reply = self._reply(result)
        return result

    # ------------------------------------------------------------------ one link

    async def _judge(
        self, store: LinkStore, scope: WorkspaceScope, url: str, index: int, seen: set[str]
    ) -> SavedLink:
        host = host_for_log(url)
        canonical = canonical_url(url)
        if index >= self._settings.max_links:
            return self._refused(url, host, "too_many_links")
        try:
            parse_url(url, allow_hosts=self._settings.allow_hosts)
        except FetchRefusedError as exc:
            return self._refused(url, host, exc.rule)
        if self._rate is not None and await self._rate(scope) is not None:
            return self._refused(url, host, "rate_limited")
        if canonical in seen:
            return SavedLink(
                uuid.UUID(int=0), url, FetchStatus.PENDING, host, duplicate_of=uuid.UUID(int=0)
            )
        seen.add(canonical)
        existing = await store.by_canonical(canonical)
        if existing is not None:
            return SavedLink(
                existing.item_id, url, FetchStatus.PENDING, host, duplicate_of=existing.item_id
            )
        return SavedLink(uuid.UUID(int=0), url, FetchStatus.PENDING, host)

    @staticmethod
    def _refused(url: str, host: str, rule: str) -> SavedLink:
        return SavedLink(
            uuid.UUID(int=0),
            url,
            FetchStatus.REFUSED,
            host,
            reason=RULE_LABELS.get(rule, rule),
            rule=rule,
        )

    def _item(
        self,
        *,
        scope: WorkspaceScope,
        turn_id: uuid.UUID,
        url: str,
        said: str,
        now: datetime,
        link: SavedLink,
    ) -> tuple[CreateItem, LinkSource]:
        video = video_site(url)
        fmt = ResourceFormat.VIDEO if video else ResourceFormat.LINK
        host = site_name(link.host) or "a link"
        title = f"Link: {host}"
        text = said if said.replace("[link]", "").strip() else f"A link to {host}"
        content = ItemContent(
            kind=Kind.RESOURCE,
            state=initial_state(Kind.RESOURCE),
            source=Source.LINK,
            trust=Trust.USER_STATED,
            format=fmt,
            content_ref=url,
            text=text,
            title=title,
            mentioned_at=now,
        )
        item = CreateItem(
            item_id=new_id(),
            item=content,
            title=title,
            rationale="a link the person pasted; the page is read afterwards",
        )
        refused = link.status is FetchStatus.REFUSED
        source = LinkSource(
            id=new_id(),
            workspace_id=scope.workspace_id,
            item_id=item.item_id,
            turn_id=turn_id,
            url=url,
            canonical_url=canonical_url(url),
            kind=LinkKind.VIDEO if video else LinkKind.LINK,
            fetch_status=link.status,
            fetch_reason=link.reason if refused else None,
            final_host=None,
            detail={"rule": link.rule} if refused else {},
        )
        return item, source

    # ------------------------------------------------------------------ what the person sees

    async def _report(self, trail: Trail, result: LinkSaveResult) -> None:
        async with trail.run(AgentStep.FETCH) as step:
            if result.links and all(link.status is FetchStatus.REFUSED for link in result.links):
                step.status = StepStatus.REFUSED
            for link in result.links:
                if link.duplicate_of is not None:
                    continue
                refused = link.status is FetchStatus.REFUSED
                if refused:
                    await trail.emit(
                        fetch_tool_call(
                            host=link.host, rule=link.rule, reason=link.reason, refused=True
                        )
                    )
                await trail.emit(
                    FetchEvent(
                        item_id=link.item_id,
                        status="refused" if refused else "pending",
                        host=link.host,
                        message=(
                            f"Didn't open it: {link.reason}." if refused else "Reading the page."
                        ),
                        reason=link.reason,
                        rule=link.rule,
                        video=video_site(link.url) is not None,
                    )
                )

    @staticmethod
    def _reply(result: LinkSaveResult) -> str:
        lines: list[str] = []
        pending = result.pending
        fresh = [link for link in pending if link.duplicate_of is None]
        if len(fresh) == 1:
            lines.append(PENDING_REPLY)
        elif fresh:
            lines.append(f"Saved {len(fresh)} links. Reading them now.")
        for link in result.links:
            if link.status is FetchStatus.REFUSED:
                lines.append(f"Saved the link, but didn't open it: {link.reason}.")
            elif link.duplicate_of is not None:
                lines.append("You already saved that link.")
        return " ".join(dict.fromkeys(lines))
