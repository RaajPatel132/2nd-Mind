"""Recall golden cases (S3.15), the seed of the S5 retrieval suite.

Each case in ``evals/cases/retrieval/*.yaml`` asks one question of the recall fixture
(``evals/fixtures/recall.yaml``) at a frozen now, and names the shape it expects, the gold items
(fixture keys) the answer must draw on, forbidden items, and an exact aggregate or
``must_abstain``. The case's ``model`` block records the plan (and any rerank scores) the fake
replays, so with the fake provider a case checks the deterministic pipeline: tools, SQL, fusion,
relaxation, selection, citations and abstention. With a live router every model step goes to
the configured providers and :func:`report` prints the baseline table (no gate).

Cases run as whole chat turns through ``TurnRunner`` on Postgres, since the tools are SQL. Read
cases share one seeded workspace; a case that writes (``fresh: true``) gets its own.
"""

import statistics
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from secondmind.agent import StoredEvent, Turn, TurnRunner
from secondmind.agent.adapters import SqlTurnStore
from secondmind.auth import IdentityStore
from secondmind.config import (
    DEFAULT_RESOURCES_DIR,
    PromptRegistry,
    Step,
    read_price_table,
    read_routing_file,
    resolve_routing,
)
from secondmind.core import (
    CitationsEvent,
    ModelCallEvent,
    RetrievalEvent,
    TriggerState,
    WorkspaceScope,
    new_id,
)
from secondmind.evals.fixture import Seeded, load_fixture, local_instant, seed_workspace
from secondmind.ingestion import IngestSettings, offline_responders, replay_key
from secondmind.memory import Memory
from secondmind.memory.adapters import EMBED_DIMENSIONS, Database, sql_memory
from secondmind.observability import NullTracer
from secondmind.providers import FakeProvider, FakeScript, ModelRouter, ResiliencePolicy
from secondmind.retrieval import (
    NO_EVIDENCE,
    RecallSettings,
    recall_responders,
    recall_text_responders,
)
from secondmind.retrieval.adapters import SqlConversationStore, SqlRecallStore

CASES_DIR = DEFAULT_RESOURCES_DIR / "evals" / "cases" / "retrieval"
ABSTAIN_PREFIX = NO_EVIDENCE.split("{", 1)[0].strip()


@dataclass(frozen=True, slots=True)
class RecallCase:
    id: str
    title: str
    question: str
    now: datetime
    timezone: str
    shape: tuple[str, ...]
    gold: tuple[str, ...]
    forbidden: tuple[str, ...]
    aggregate: float | None
    must_abstain: bool
    reply_contains: tuple[str, ...]
    fired: tuple[str, ...]
    not_fired: tuple[str, ...]
    fresh: bool
    setup: tuple[str, ...]
    model: Mapping[str, Any]
    path: Path


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)  # type: ignore[attr-defined]


def load_cases(directory: Path = CASES_DIR) -> list[RecallCase]:
    fixture = load_fixture()
    cases = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tz = str(raw.get("timezone", fixture.timezone))
        agg = raw.get("aggregate")
        cases.append(
            RecallCase(
                id=str(raw.get("id", path.stem)),
                title=str(raw.get("title", path.stem)),
                question=str(raw["question"]),
                now=local_instant(str(raw.get("now", fixture.now)), tz),
                timezone=tz,
                shape=_strings(raw.get("shape")),
                gold=_strings(raw.get("gold")),
                forbidden=_strings(raw.get("forbidden")),
                aggregate=None if agg is None else float(agg),
                must_abstain=bool(raw.get("must_abstain", False)),
                reply_contains=_strings(raw.get("reply_contains")),
                fired=_strings(raw.get("fired")),
                not_fired=_strings(raw.get("not_fired")),
                fresh=bool(raw.get("fresh", False)) or bool(raw.get("setup")),
                setup=tuple(str(s["input"]) for s in raw.get("setup", [])),
                model=raw.get("model") or {},
                path=path,
            )
        )
    return cases


def case_replay(cases: Sequence[RecallCase]) -> dict[str, dict[str, Any]]:
    """Question -> recorded model outputs, for the offline brain (intent, plan, rerank, and
    extract for setup turns that save)."""
    replay: dict[str, dict[str, Any]] = {}
    for case in cases:
        replay.setdefault(replay_key(case.question), dict(case.model))
        path_raw = yaml.safe_load(case.path.read_text(encoding="utf-8")) or {}
        for turn in path_raw.get("setup", []):
            replay.setdefault(replay_key(str(turn["input"])), dict(turn.get("model") or {}))
    return replay


def fake_router(
    cases: Sequence[RecallCase], resources: Path = DEFAULT_RESOURCES_DIR
) -> ModelRouter:
    """Every step on the offline fake, replaying what the cases recorded."""
    routing = resolve_routing(read_routing_file(resources / "config" / "models.yaml"), {}, "fake")
    replay = case_replay(cases)
    script = FakeScript(
        responders=offline_responders(replay) | recall_responders(replay),
        text_responders=recall_text_responders(),
    )
    return ModelRouter(
        routing=routing,
        prices=read_price_table(resources / "config" / "prices.yaml"),
        adapters={"fake": FakeProvider("fake", script=script)},
        policy=ResiliencePolicy(max_retries=0),
    )


# ------------------------------------------------------------------ seeding


def router_embedder(
    router: ModelRouter, dimensions: int = EMBED_DIMENSIONS
) -> tuple[Callable[[Sequence[str], int], Awaitable[list[list[float]] | None]], str]:
    """Embeddings through the router's ``embed`` route, and the model name keys are stored
    under (the same one recall queries with)."""
    route = router.route(Step.EMBED)

    async def embed(texts: Sequence[str], cache_hits: int = 0) -> list[list[float]] | None:
        if not texts:
            return []
        return (await router.embed(list(texts), dimensions=dimensions)).vectors

    return embed, f"{route.primary.provider}:{route.primary.model}@{dimensions}"


async def seed_main(db: Database, identity: IdentityStore, router: ModelRouter) -> Seeded:
    """A new user with the fixture's main workspace, seeded."""
    fixture = load_fixture()
    user, ws = await identity.ensure_user_with_private_workspace(
        email=f"recall-{new_id()}@example.test", timezone=fixture.timezone
    )
    scope = WorkspaceScope(workspace_id=ws.id, user_id=user.id)
    embed, model = router_embedder(router)
    return await seed_workspace(
        fixture,
        memory=Memory(sql_memory(db)),
        turns=SqlTurnStore(db, scope),
        scope=scope,
        timezone=fixture.timezone,
        now=fixture.instant,
        embed=embed,
        embedding_model=model,
        conversation=SqlConversationStore(db, scope),
    )


# ------------------------------------------------------------------ running


@dataclass(slots=True)
class RecallRun:
    case: RecallCase
    turn: Turn
    reply: str
    retrieval: RetrievalEvent | None
    citations: CitationsEvent | None
    calls: list[ModelCallEvent]
    ranked: list[str]  # fixture keys of the selected items, in answer order
    ranked_all: list[str]  # fixture keys of every candidate, best first
    soft_only: list[str]
    fired: list[str]
    latency_ms: int
    cost_usd: Decimal
    seeded_items: dict[str, uuid.UUID] = field(default_factory=dict)

    @property
    def shapes(self) -> list[str]:
        return [s.shape.value for s in self.retrieval.sub_queries] if self.retrieval else []

    @property
    def aggregate(self) -> float | None:
        if self.retrieval is None:
            return None
        for sq in self.retrieval.sub_queries:
            if sq.aggregate is not None and sq.aggregate.value is not None:
                return float(sq.aggregate.value)
        return None

    @property
    def abstained(self) -> bool:
        return not self.ranked and self.aggregate is None

    @property
    def relaxed(self) -> bool:
        return bool(self.retrieval) and any(
            any(r.count for r in sq.relaxation)
            for sq in self.retrieval.sub_queries  # type: ignore[union-attr]
        )

    @property
    def ttft_ms(self) -> int | None:
        answer = [c for c in self.calls if c.step == Step.ANSWER.value]
        return answer[0].time_to_first_token_ms if answer else None


def _events(stored: Sequence[StoredEvent]) -> tuple[Any, ...]:
    retrieval = next((e.event for e in stored if isinstance(e.event, RetrievalEvent)), None)
    cites = next((e.event for e in stored if isinstance(e.event, CitationsEvent)), None)
    calls = [e.event for e in stored if isinstance(e.event, ModelCallEvent)]
    return retrieval, cites, calls


async def run_case(
    case: RecallCase,
    *,
    db: Database,
    seeded: Seeded,
    router: ModelRouter,
    settings: RecallSettings | None = None,
    resources: Path = DEFAULT_RESOURCES_DIR,
) -> RecallRun:
    scope = seeded.scope
    memory = Memory(sql_memory(db))

    def frozen(at: datetime = case.now) -> datetime:
        return at

    runner = TurnRunner(
        router=router,
        prompts=PromptRegistry.load(resources / "prompts"),
        stores=lambda s: SqlTurnStore(db, s),
        tracer=NullTracer(),
        config_hash="eval",
        max_message_chars=8000,
        memory=memory,
        ingest=IngestSettings(),
        embed_dimensions=EMBED_DIMENSIONS,
        clock=frozen,
        recall_stores=lambda s: SqlRecallStore(db, s, timeout_ms=10_000),
        recall=settings or RecallSettings(),
        conversation_stores=lambda s: SqlConversationStore(db, s),
    )
    try:
        for text in case.setup:
            handle = await runner.start(scope, text=text, timezone=case.timezone)
            async for _ in handle.events():
                pass
        started = time.perf_counter()
        handle = await runner.start(scope, text=case.question, timezone=case.timezone)
        async for _ in handle.events():
            pass
        elapsed = round((time.perf_counter() - started) * 1000)
    finally:
        await runner.aclose()
    store = SqlTurnStore(db, scope)
    turn = await store.get(handle.turn.id)
    if turn is None:
        raise RuntimeError(f"case {case.id} produced no turn")
    retrieval, cites, calls = _events(await store.events(turn.id))
    key_of = {v: k for k, v in seeded.items.items()}
    ranked: list[str] = []
    ranked_all: list[str] = []
    soft: list[str] = []
    for sq in retrieval.sub_queries if retrieval else []:
        for c in sq.candidates:
            key = key_of.get(c.item_id) if c.item_id else None
            if key is None:
                continue
            ranked_all.append(key)
            if c.selected:
                ranked.append(key)
                if c.soft_only:
                    soft.append(key)
    triggers = await memory.reader(scope).triggers(list(seeded.items.values()))
    fired = sorted(
        key_of[t.item_id] for t in triggers if t.state is TriggerState.FIRED and t.item_id in key_of
    )
    return RecallRun(
        case=case,
        turn=turn,
        reply=turn.output or "",
        retrieval=retrieval,
        citations=cites,
        calls=calls,
        ranked=list(dict.fromkeys(ranked)),
        ranked_all=list(dict.fromkeys(ranked_all)),
        soft_only=soft,
        fired=fired,
        latency_ms=elapsed,
        cost_usd=turn.usage.cost_usd,
        seeded_items=seeded.items,
    )


# ------------------------------------------------------------------ checking


def failures(run: RecallRun) -> list[str]:
    """What a case expected and didn't get (empty: it passed)."""
    case, out = run.case, []
    if case.shape and not set(case.shape) <= set(run.shapes):
        out.append(f"shape: expected {list(case.shape)}, planned {run.shapes}")
    missing = [g for g in case.gold if g not in run.ranked]
    if missing:
        out.append(f"gold not in the answer: {missing} (selected {run.ranked})")
    leaked = [f for f in case.forbidden if f in run.ranked]
    if leaked:
        out.append(f"forbidden in the answer: {leaked}")
    if case.aggregate is not None and run.aggregate != case.aggregate:
        out.append(f"aggregate: expected {case.aggregate}, got {run.aggregate}")
    if case.must_abstain and not run.abstained:
        out.append(f"should abstain; selected {run.ranked}")
    if case.must_abstain and ABSTAIN_PREFIX not in run.reply:
        out.append(f"abstention reply missing: {run.reply[:120]!r}")
    if not case.must_abstain and case.gold and run.abstained:
        out.append("abstained but gold exists")
    out.extend(
        f"reply lacks {text!r}: {run.reply[:160]!r}"
        for text in case.reply_contains
        if text.lower() not in run.reply.lower()
    )
    out.extend(f"trigger {k} did not fire" for k in case.fired if k not in run.fired)
    out.extend(f"trigger {k} fired" for k in case.not_fired if k in run.fired)
    if run.turn.status.value != "completed":
        out.append(f"turn {run.turn.status.value}")
    return out


def explain(run: RecallRun) -> str:
    """Every candidate with the channels that found it, its rerank score and whether it was
    selected: what a failing case prints."""
    key_of = {v: k for k, v in run.seeded_items.items()}
    lines = []
    for sq in run.retrieval.sub_queries if run.retrieval else []:
        lines.append(f"part {sq.index} {sq.shape.value}: relaxed {[r.step for r in sq.relaxation]}")
        for c in sq.candidates:
            key = key_of.get(c.item_id) if c.item_id else None
            score = "-" if c.rerank_score is None else f"{c.rerank_score:.2f}"
            mark = "*" if c.selected else " "
            found = ",".join(f"{f.channel}#{f.rank}" for f in c.found_by)
            lines.append(f" {mark} {key or c.title}: {found} rerank {score}")
    return "\n".join(lines)


def _pct(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def report(runs: Sequence[RecallRun]) -> str:
    """hit@5 and MRR by shape, no-answer accuracy, soft-only and relaxation rates, p50/p95
    time to first token and total, cost per recall turn by step, and the failures."""
    by_shape: dict[str, list[RecallRun]] = defaultdict(list)
    for run in runs:
        if run.case.gold:
            by_shape[run.case.shape[0] if run.case.shape else "?"].append(run)
    lines = ["shape          cases  hit@5   MRR"]
    for shape, group in sorted(by_shape.items()):
        hits = [any(g in r.ranked_all[:5] for g in r.case.gold) for r in group]
        rr = [
            next((1 / (n + 1) for n, k in enumerate(r.ranked_all) if k in r.case.gold), 0.0)
            for r in group
        ]
        hit5, mrr = sum(hits) / len(group), statistics.mean(rr)
        lines.append(f"{shape:<14} {len(group):>5}  {hit5:>5.2f}  {mrr:>5.2f}")
    no_answer = [r for r in runs if r.case.must_abstain]
    selected = [k for r in runs for k in r.ranked]
    soft = [k for r in runs for k in r.soft_only]
    ttft = [float(r.ttft_ms) for r in runs if r.ttft_ms is not None]
    total = [float(r.latency_ms) for r in runs]
    cost_by_step: dict[str, Decimal] = defaultdict(Decimal)
    for r in runs:
        for c in r.calls:
            cost_by_step[c.step] += c.usage.cost_usd
    n = max(1, len(runs))
    failed = [(r.case.id, f) for r in runs for f in failures(r)]
    lines += [
        "",
        f"no-answer accuracy   {sum(r.abstained for r in no_answer)}/{len(no_answer)}",
        f"soft-only rate       {len(soft) / max(1, len(selected)):.2f} of selected items",
        f"relaxation rate      {sum(r.relaxed for r in runs) / n:.2f} of turns",
        f"time to first token  p50 {_pct(ttft, 0.5):.0f} ms, p95 {_pct(ttft, 0.95):.0f} ms",
        f"turn total           p50 {_pct(total, 0.5):.0f} ms, p95 {_pct(total, 0.95):.0f} ms",
        f"cost per recall turn ${sum(r.cost_usd for r in runs) / n:.5f}",
        *(
            f"  {step:<10} ${cost / n:.5f}"
            for step, cost in sorted(cost_by_step.items(), key=lambda kv: -kv[1])
        ),
        f"cases passed         {len(runs) - len({c for c, _ in failed})}/{len(runs)}",
        *(f"  {case}: {why}" for case, why in failed),
    ]
    return "\n".join(lines)


def case_ids(cases: Sequence[RecallCase]) -> list[str]:
    return [c.id for c in cases]


def unknown_keys(cases: Sequence[RecallCase]) -> dict[str, list[str]]:
    """Case -> gold/forbidden/trigger keys that aren't fixture items (a typo in a case)."""
    known = {i.key for i in load_fixture().items}
    out: dict[str, list[str]] = {}
    for c in cases:
        bad = [k for k in (*c.gold, *c.forbidden, *c.fired, *c.not_fired) if k not in known]
        if bad:
            out[c.id] = bad
    return out


__all__ = [
    "CASES_DIR",
    "RecallCase",
    "RecallRun",
    "case_ids",
    "explain",
    "failures",
    "fake_router",
    "load_cases",
    "report",
    "router_embedder",
    "run_case",
    "seed_main",
    "unknown_keys",
]
