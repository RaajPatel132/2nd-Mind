"""Test doubles for the recall pipeline: a store that records every tool call and returns what a
test scripts, and a small in-memory workspace to hydrate from."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from secondmind.config import DEFAULT_RESOURCES_DIR, PromptRegistry
from secondmind.core import (
    EntityRole,
    Kind,
    NullTrail,
    TurnEvent,
    VocabKind,
    WorkspaceScope,
    new_id,
)
from secondmind.ingestion import ModelSteps, TurnNow
from secondmind.memory import CoreView, Memory, RelateEntities, WriterTurn
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer
from secondmind.providers import FakeOutcome, FakeProvider, FakeScript, ModelCall
from secondmind.retrieval import (
    Access,
    AggregateResult,
    ConversationHit,
    EntityResult,
    Filters,
    HistoryResult,
    Hit,
    LookupResult,
    PathHop,
    Query,
    RecallContext,
    RecallPipeline,
    RecallSettings,
    SetOp,
    TimelineResult,
    WindowFilter,
    complete_plan,
    recall_responders,
    recall_text_responders,
)
from tests.unit.memory.helpers import create, item, person
from tests.unit.providers.helpers import router as make_router

PROMPTS = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
NOW = TurnNow(datetime(2026, 10, 6, 4, 30, tzinfo=UTC), "Asia/Kolkata")  # Tue 10:00 IST


@dataclass
class Call:
    tool: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass
class RecordingStore:
    """A RecallStore that records calls; results are scripted per tool (default: nothing)."""

    results: dict[str, Any] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)

    def _take(self, tool: str, default: Any, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(Call(tool, args, kwargs))
        if tool in self.fail:
            raise RuntimeError(f"{tool} is down")
        value = self.results.get(tool, default)
        return value(*args, **kwargs) if callable(value) else value

    async def lookup(
        self, filters: Filters, access: Access, *, set_op: SetOp | None = None, limit: int = 50
    ) -> LookupResult:
        return self._take(
            "lookup", LookupResult([], 0), filters, access, set_op=set_op, limit=limit
        )

    async def aggregate(self, filters: Filters, access: Access, **kw: Any) -> AggregateResult:
        return self._take(
            "aggregate", AggregateResult(op=kw["op"], value=0.0, item_ids=[]), filters, access, **kw
        )

    async def search(
        self, query: Query, filters: Filters, access: Access, *, limit: int
    ) -> list[Hit]:
        return self._take("search", [], query, filters, access, limit=limit)

    async def entity(
        self,
        entity_ids: Sequence[uuid.UUID],
        access: Access,
        *,
        path: Sequence[PathHop] = (),
        limit: int = 50,
    ) -> EntityResult:
        return self._take(
            "entity", EntityResult([], [], []), list(entity_ids), access, path=list(path)
        )

    async def timeline(
        self, window: WindowFilter, filters: Filters, access: Access, **kw: Any
    ) -> TimelineResult:
        return self._take("timeline", TimelineResult([]), window, filters, access, **kw)

    async def history(self, access: Access, **kw: Any) -> HistoryResult:
        return self._take("history", HistoryResult([]), access, **kw)

    async def conversation(self, query: Query, **kw: Any) -> list[ConversationHit]:
        return self._take("conversation", [], query, **kw)

    def of(self, tool: str) -> list[Call]:
        return [c for c in self.calls if c.tool == tool]

    @property
    def tools(self) -> list[str]:
        return sorted({c.tool for c in self.calls})


class Sink:
    def __init__(self) -> None:
        self.events: list[TurnEvent] = []

    async def __call__(self, event: TurnEvent) -> None:
        self.events.append(event)

    def of(self, kind: str) -> list[TurnEvent]:
        return [e for e in self.events if e.type == kind]


@dataclass
class World:
    """A small workspace: Nisha (sister), Rohan (spouse_of Nisha) and a few memories."""

    memory: Memory
    scope: WorkspaceScope
    ids: dict[str, uuid.UUID]


async def world() -> World:
    db = InMemoryMemory()
    memory = Memory(db.store)
    scope = WorkspaceScope(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
    me = (await memory.reader(scope).self_entity()).id
    nisha = person("Nisha", "sister", key=True)
    rohan = person("Rohan")
    pune = create(item("I live in Pune", Kind.FACT, predicate="lives_in", subject_entity_id=me))
    jazz = create(
        item("Rohan loves live jazz", Kind.PREFERENCE, subject_entity_id=rohan.entity_id),
        (rohan.entity_id, EntityRole.ABOUT),
    )
    run = create(
        item(
            "I ran 5 km",
            Kind.EPISODE,
            subtype="measurement",
            attributes={"activity": "run"},
            occurred_start=datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        )
    )
    turn = WriterTurn(
        turn_id=new_id(), workspace_id=scope.workspace_id, kind="system", now=NOW.instant
    )
    writer = memory.writer(scope, turn, confirmed=True)
    writer.add(
        nisha,
        rohan,
        pune,
        jazz,
        run,
        RelateEntities(
            relation_id=new_id(),
            src_entity_id=rohan.entity_id,
            relation="spouse_of",
            dst_entity_id=nisha.entity_id,
            title="Rohan spouse_of Nisha",
        ),
    )
    writer.register_vocab(VocabKind.PREDICATE, "lives_in")
    writer.register_vocab(VocabKind.SUBTYPE, "measurement")
    writer.register_vocab(VocabKind.SUBTYPE, "watch")
    writer.register_vocab(VocabKind.RELATION, "spouse_of")
    await writer.commit()
    return World(
        memory=memory,
        scope=scope,
        ids={
            "me": me,
            "nisha": nisha.entity_id,
            "rohan": rohan.entity_id,
            "pune": pune.item_id,
            "jazz": jazz.item_id,
            "run": run.item_id,
        },
    )


def fake(*outcomes: tuple[str, FakeOutcome]) -> FakeProvider:
    script = FakeScript(responders=recall_responders(), text_responders=recall_text_responders())
    for step, outcome in outcomes:
        script.add(outcome, step=step)
    return FakeProvider("primary", script=script)


def plan(*sub_queries: dict[str, Any]) -> FakeOutcome:
    return FakeOutcome(structured=complete_plan({"sub_queries": list(sub_queries)}))


def steps(provider: FakeProvider, calls: list[ModelCall] | None = None) -> ModelSteps:
    async def record(call: ModelCall, span: object, prompt: object, output: str) -> None:
        if calls is not None:
            calls.append(call)

    return ModelSteps(
        router=make_router({"primary": provider}, "primary:m", None, all_steps=True),
        prompts=PROMPTS,
        trace=NullTracer().start_turn(
            turn_id=new_id(),
            workspace_id=new_id(),
            user_id=new_id(),
            turn_input=None,
            metadata={},
        ),
        record=record,  # type: ignore[arg-type]
        now=NOW.instant,
    )


@dataclass
class Ran:
    reply: str
    events: Sink
    store: RecordingStore
    calls: list[ModelCall]
    outcome: Any


async def run_recall(
    w: World,
    message: str,
    provider: FakeProvider,
    store: RecordingStore | None = None,
    *,
    settings: RecallSettings | None = None,
    core: str = "## Core memory",
) -> Ran:
    store = store or RecordingStore()
    sink = Sink()
    written: list[str] = []
    calls: list[ModelCall] = []
    outcome = await RecallPipeline(settings).run(
        RecallContext(
            scope=w.scope,
            turn_id=new_id(),
            message=message,
            now=NOW,
            steps=steps(provider, calls),
            memory=w.memory,
            store=store,
            core=CoreView(text=core, tokens=1, item_ids=()),
            trail=NullTrail(sink),
            write=written.append,
        )
    )
    return Ran(reply="".join(written), events=sink, store=store, calls=calls, outcome=outcome)
