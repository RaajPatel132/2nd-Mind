"""Reading a saved link in the worker (S4.7): everything the read produces is written to the
**original turn's** write log, so undoing the turn that saved the link removes the item, its page
text and its chunks. The spend gate and the quota treat it as part of that save: its model calls go
on that turn's ledger, as the person's cost, because it's their save.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from secondmind.agent.trail import TurnTrail
from secondmind.agent.turns import TurnStore, TurnStoreFactory
from secondmind.config import PromptRegistry, Step
from secondmind.core import (
    AgentStep,
    Clock,
    FetchEvent,
    ItemStatus,
    KeyKind,
    StepStatus,
    Trust,
    WorkspaceScope,
    new_id,
)
from secondmind.ingestion import ModelSteps
from secondmind.links import (
    DigestFn,
    DigestOutput,
    DigestVars,
    FetchStatus,
    LinkReader,
    LinkReading,
    LinkSource,
    ReadResult,
    ReadSettings,
    host_for_log,
)
from secondmind.memory import Memory, UpdateItem, WriterTurn
from secondmind.observability import GenerationSpan, NullTracer, get_logger
from secondmind.providers import (
    CallsRefusedError,
    ChatMessage,
    ModelCall,
    ModelRouter,
    ProviderUnavailableError,
)

log = get_logger(__name__)

STEP_STATUS = {
    FetchStatus.FULL: StepStatus.DONE,
    FetchStatus.PARTIAL: StepStatus.DONE,
    FetchStatus.FAILED: StepStatus.FAILED,
    FetchStatus.REFUSED: StepStatus.REFUSED,
}


@dataclass(frozen=True, slots=True)
class LinkWorkflow:
    reading: LinkReading
    router: ModelRouter
    prompts: PromptRegistry
    stores: TurnStoreFactory
    memory: Memory
    clock: Clock
    embed_dimensions: int

    async def run(
        self,
        scope: WorkspaceScope,
        item_id: uuid.UUID,
        *,
        timezone: str,
        pasted: str | None = None,
    ) -> ReadResult | None:
        """Read the link of ``item_id`` (or, with ``pasted``, use the text the person added).
        None when there is nothing to do: no such link, already read, or the item was undone."""
        links = self.reading.stores(scope)
        source = await links.by_item(item_id)
        if source is None or (pasted is None and source.fetch_status is not FetchStatus.PENDING):
            return None
        item = await self.memory.reader(scope).item(item_id)
        if item is None or item.status is not ItemStatus.ACTIVE:
            return None  # the turn that saved it was undone: nothing to read
        turns = self.stores(scope)
        turn = await turns.get(source.turn_id)
        if turn is None:
            return None
        started = self.clock()
        steps = self._steps(scope, turns, source)
        reader = LinkReader(
            self.reading.fetcher,
            self._digest(steps),
            self._embed(steps),
            ReadSettings(
                chunk_tokens=self.reading.settings.chunk_tokens,
                max_chunks=self.reading.settings.max_chunks,
                digest_chars=self.reading.settings.digest_chars,
                embedding_model=steps.embedding_model,
            ),
        )
        said = item.text
        result = (
            await reader.read_text(source, person_said=said, text=pasted)
            if pasted is not None
            else await reader.read(source, person_said=said)
        )
        await links.update(item_id, **result.source_changes(fetched_at=self.clock()))
        await links.replace_chunks(item_id, result.chunks)
        if result.status in (FetchStatus.FULL, FetchStatus.PARTIAL) and result.title:
            await self._write_item(scope, source, turn.started_at, turn.kind.value, result)
            await self._rebuild_keys(scope, item_id, result, steps, timezone)
        await self._report(turns, source, result, started)
        return result

    async def embed_pending(self, scope: WorkspaceScope) -> int:
        """Embed page passages that were saved without a vector (the provider was down when the
        page was read). Their cost is the app's (system): nobody asked for this call."""
        links = self.reading.stores(scope)
        pending = await links.unembedded_chunks()
        if not pending:
            return 0
        turns = self.stores(scope)
        by_item: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
        for key_id, item_id, chunk_text in pending:
            by_item.setdefault(item_id, []).append((key_id, chunk_text))
        done = 0
        for item_id, keys in by_item.items():
            source = await links.by_item(item_id)
            item = await self.memory.reader(scope).item(item_id)
            if source is None or item is None or item.status is not ItemStatus.ACTIVE:
                continue
            steps = self._steps(scope, turns, source, system=True)
            try:
                vectors = await steps.embed([f"{item.title}\n\n{t}" for _, t in keys])
            except CallsRefusedError:
                raise
            except ProviderUnavailableError:
                continue  # still down: the next run tries again
            if vectors is None or len(vectors) != len(keys):
                continue
            await links.set_embeddings(
                [(key_id, v) for (key_id, _), v in zip(keys, vectors, strict=True)],
                steps.embedding_model,
            )
            done += len(keys)
        return done

    # ------------------------------------------------------- the item, on the original turn

    async def _write_item(
        self,
        scope: WorkspaceScope,
        source: LinkSource,
        turn_started_at: datetime,
        turn_kind: str,
        result: ReadResult,
    ) -> None:
        writer = self.memory.writer(
            scope,
            WriterTurn(
                turn_id=source.turn_id,
                workspace_id=scope.workspace_id,
                kind=turn_kind,
                now=turn_started_at,
            ),
        )
        writer.add(
            UpdateItem(
                item_id=source.item_id,
                changes={"title": result.title, "summary": result.summary, "tags": result.tags},
                title=f"read the page: {result.title}"[:120],
                rationale="the page, read after the save; this is what the page says, not what "
                "the person said",
                trust=Trust.CONTENT_DERIVED,
                origin="content",
            )
        )
        await writer.commit()

    async def _rebuild_keys(
        self,
        scope: WorkspaceScope,
        item_id: uuid.UUID,
        result: ReadResult,
        steps: ModelSteps,
        timezone: str,
    ) -> None:
        extra = {item_id: [(KeyKind.CUE, c) for c in result.cues]}

        async def embed(texts: Sequence[str], hits: int) -> list[list[float]] | None:
            try:
                return await steps.embed(list(texts), hits)
            except CallsRefusedError:
                raise
            except ProviderUnavailableError:
                return None

        indexer = self.memory.keys(
            scope, timezone=timezone, embed=embed, model=steps.embedding_model
        )
        await indexer.rebuild([item_id], extra=extra)

    # ------------------------------------------------------------------ the turn's Trail

    async def _report(
        self, turns: TurnStore, source: LinkSource, result: ReadResult, started: datetime
    ) -> None:
        trail = TurnTrail(turns, source.turn_id, clock=self.clock)
        event = FetchEvent(
            item_id=source.item_id,
            status=result.status.value,
            host=result.final_host or host_for_log(source.url),
            message=result.message,
            reason=result.reason,
            rule=result.rule,
            title=result.title or None,
            site=result.site,
            word_count=result.word_count,
            status_code=result.status_code,
            bytes=result.bytes_read,
            redirects=result.redirects,
            content_type=result.content_type,
            extraction_method=result.extraction_method,
            chunks=len(result.chunks) if result.chunks else None,
            video=result.video,
        )
        elapsed = int((self.clock() - started).total_seconds() * 1000)
        await trail.report(
            AgentStep.FETCH,
            status=STEP_STATUS[result.status],
            started_at=started,
            latency_ms=elapsed,
            events=[event],
        )

    # ------------------------------------------------------------------ model steps

    def _steps(
        self,
        scope: WorkspaceScope,
        turns: TurnStore,
        source: LinkSource,
        *,
        system: bool = False,
    ) -> ModelSteps:
        async def on_ledger(
            call: ModelCall, span: GenerationSpan, prompt: object, output: str
        ) -> None:
            # The read belongs to the turn that saved the link: its calls go on that turn's ledger
            # as the person's cost (their save), and so count against their quota.
            await turns.record_usage(source.turn_id, call.to_event(), system=system)

        return ModelSteps(
            router=self.router,
            prompts=self.prompts,
            trace=NullTracer().start_turn(
                turn_id=new_id(),
                workspace_id=scope.workspace_id,
                user_id=scope.user_id,
                turn_input=None,
                metadata={},
            ),
            record=on_ledger,
            now=self.clock(),
            embed_dimensions=self.embed_dimensions,
        )

    @staticmethod
    def _digest(steps: ModelSteps) -> DigestFn:
        async def digest(*, source: str, person_said: str, material: str) -> DigestOutput | None:
            try:
                return await steps.structured(
                    Step.DIGEST,
                    DigestOutput,
                    DigestVars(source=source, person_said=_quote(person_said)),
                    [ChatMessage.user(material)],
                )
            except CallsRefusedError:
                raise  # the spend gate said no: the job is deferred, not half done
            except ProviderUnavailableError:
                return None  # the page's own title and description stand in

        return digest

    @staticmethod
    def _embed(steps: ModelSteps) -> Callable[[Sequence[str]], Awaitable[list[list[float]] | None]]:
        async def embed(texts: Sequence[str]) -> list[list[float]] | None:
            try:
                return await steps.embed(list(texts))
            except CallsRefusedError:
                raise
            except ProviderUnavailableError:
                return None  # the chunks are saved without vectors; a later job embeds them

        return embed


def _quote(person_said: str) -> str:
    """What the person said, for the prompt's quotation (short, one line, no stray quotes)."""
    return " ".join(person_said.replace('"', "'").split())[:300]


__all__ = ["LinkWorkflow"]
