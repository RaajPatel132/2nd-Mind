"""Ingestion golden cases (S2.13), the seed of the S5 eval harness.

Each case in ``evals/cases/ingest/*.yaml`` gives an input, a frozen now + timezone, earlier turns
that build the workspace state (``setup``), the model outputs to replay, and the expected
memories. With the fake provider the case runs the whole pipeline (resolver, normalisation,
entity resolution, reconciliation, writer, policy, diff, keys) on those fixed outputs, as an
ordinary test. With ``--live`` the final turn goes to the configured real provider instead (the
setup still replays, so every case starts from the same state) and a per-field score table is
printed, with p50/p95 latency and cost.
"""

import os
import statistics
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from secondmind.agent import StoredEvent, Turn, TurnRunner
from secondmind.agent.adapters import InMemoryTurns
from secondmind.config import (
    DEFAULT_RESOURCES_DIR,
    PriceTable,
    PromptRegistry,
    read_price_table,
    read_routing_file,
    resolve_routing,
)
from secondmind.core import (
    DecisionEvent,
    EntityKind,
    MemoryDiffEvent,
    TargetType,
    WorkspaceScope,
    new_id,
)
from secondmind.ingestion import IngestSettings, offline_responders, replay_key
from secondmind.memory import (
    EntityRecord,
    ItemRecord,
    Memory,
    MemorySettings,
    TriggerRecord,
    WriteLogRecord,
)
from secondmind.memory.adapters import InMemoryMemory
from secondmind.observability import NullTracer
from secondmind.providers import FakeProvider, FakeScript, ModelRouter, ResiliencePolicy
from secondmind.providers.adapters import build_adapter

CASES_DIR = DEFAULT_RESOURCES_DIR / "evals" / "cases" / "ingest"
FIELDS = ("kind", "state", "modality", "date", "entity", "category", "layer", "reconcile")


@dataclass(frozen=True, slots=True)
class TurnSpec:
    input: str
    now: datetime
    model: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class IngestCase:
    id: str
    title: str
    timezone: str
    setup: tuple[TurnSpec, ...]
    turn: TurnSpec
    expect: Mapping[str, Any]
    path: Path


def load_cases(directory: Path = CASES_DIR) -> list[IngestCase]:
    cases = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tz = str(raw.get("timezone", "Asia/Kolkata"))
        default_now = _local(str(raw.get("now", "2026-09-23T10:00")), tz)

        def spec(
            turn: Mapping[str, Any], *, tz: str = tz, fallback: datetime = default_now
        ) -> TurnSpec:
            now = _local(str(turn["now"]), tz) if turn.get("now") else fallback
            return TurnSpec(input=str(turn["input"]), now=now, model=turn.get("model") or {})

        cases.append(
            IngestCase(
                id=str(raw.get("id", path.stem)),
                title=str(raw.get("title", path.stem)),
                timezone=tz,
                setup=tuple(spec(t) for t in raw.get("setup", [])),
                turn=spec(raw),
                expect=raw.get("expect") or {},
                path=path,
            )
        )
    return cases


def _local(text: str, tz: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(tz))


# ------------------------------------------------------------------ running


@dataclass(slots=True)
class CaseRun:
    case: IngestCase
    turn: Turn
    events: list[StoredEvent]
    items: list[ItemRecord]
    entities: list[EntityRecord]
    triggers: list[TriggerRecord]
    log: list[WriteLogRecord]
    stored_text: str
    latency_ms: int
    cost_usd: Decimal
    relations: list[str] = field(default_factory=list)
    categories: dict[uuid.UUID, str] = field(default_factory=dict)
    keys: list[str] = field(default_factory=list)

    @property
    def decision(self) -> DecisionEvent | None:
        return next((e.event for e in self.events if isinstance(e.event, DecisionEvent)), None)

    @property
    def diff(self) -> MemoryDiffEvent | None:
        return next((e.event for e in self.events if isinstance(e.event, MemoryDiffEvent)), None)


def fake_router(spec: TurnSpec, resources: Path = DEFAULT_RESOURCES_DIR) -> ModelRouter:
    """Every step on the offline fake, replaying the outputs recorded for this turn."""
    routing = resolve_routing(read_routing_file(resources / "config" / "models.yaml"), {}, "fake")
    replay = {replay_key(spec.input): dict(spec.model)}
    fake = FakeProvider("fake", script=FakeScript(responders=offline_responders(replay)))
    return ModelRouter(
        routing=routing,
        prices=_prices(resources),
        adapters={"fake": fake},
        policy=ResiliencePolicy(max_retries=0),
    )


def live_router(
    environ: Mapping[str, str] | None = None, resources: Path = DEFAULT_RESOURCES_DIR
) -> ModelRouter:
    """Every step on its configured real provider (``MODEL_<STEP>`` overrides apply). Raises
    ``ConfigError`` naming the missing key when a provider has no credentials."""
    env = os.environ if environ is None else environ
    routing = resolve_routing(read_routing_file(resources / "config" / "models.yaml"), env, "live")
    used = sorted({ref.provider for ref in routing.refs()})
    return ModelRouter(
        routing=routing,
        prices=_prices(resources),
        adapters={name: build_adapter(routing.providers[name], env) for name in used},
        policy=ResiliencePolicy(max_retries=2),
    )


def _prices(resources: Path) -> PriceTable:
    return read_price_table(resources / "config" / "prices.yaml")


async def run_case(
    case: IngestCase,
    *,
    live: ModelRouter | None = None,
    resources: Path = DEFAULT_RESOURCES_DIR,
) -> CaseRun:
    prompts = PromptRegistry.load(resources / "prompts")
    memory_db = InMemoryMemory()
    memory = Memory(memory_db.store, MemorySettings())
    scope = WorkspaceScope(workspace_id=new_id(), user_id=new_id())
    turns = InMemoryTurns()
    final: Turn | None = None
    elapsed = 0
    for index, spec in enumerate([*case.setup, case.turn]):
        is_final = index == len(case.setup)
        router = live if (is_final and live is not None) else fake_router(spec, resources)
        instant = spec.now.astimezone(ZoneInfo("UTC"))

        def frozen(at: datetime = instant) -> datetime:
            return at

        turns.clock = frozen
        runner = TurnRunner(
            router=router,
            prompts=prompts,
            stores=turns.store,
            tracer=NullTracer(),
            config_hash="eval",
            max_message_chars=8000,
            memory=memory,
            ingest=IngestSettings(),
            clock=frozen,
        )
        started = time.perf_counter()
        handle = await runner.start(scope, text=spec.input, timezone=case.timezone)
        async for _ in handle.events():
            pass
        elapsed = round((time.perf_counter() - started) * 1000)
        final = await turns.store(scope).get(handle.turn.id)
    if final is None:
        raise RuntimeError(f"case {case.id} produced no turn")
    events = await turns.store(scope).events(final.id)
    tables = memory_db.tables(scope.workspace_id)
    names = {e.id: e.name for e in tables.entities.values()}
    stored = " ".join(
        [
            *(t.input + " " + (t.output or "") for t in turns.turns.values()),
            *(e.event.model_dump_json() for evs in turns.events.values() for e in evs),
            *(i.model_dump_json() for i in tables.items.values()),
            *(k.text for k in tables.keys.values()),
            *(r.model_dump_json() for r in tables.write_log),
        ]
    )
    return CaseRun(
        case=case,
        turn=final,
        events=events,
        items=list(tables.items.values()),
        entities=list(tables.entities.values()),
        triggers=list(tables.triggers.values()),
        log=[r for r in tables.write_log if r.turn_id == final.id],
        stored_text=stored,
        latency_ms=elapsed,
        cost_usd=final.usage.cost_usd,
        relations=[
            f"{names.get(r.src_entity_id)} {r.relation} {names.get(r.dst_entity_id)}"
            for r in tables.relations.values()
        ],
        categories={c.id: c.slug for c in tables.categories.values()},
        keys=[k.text for k in tables.keys.values()],
    )


# ------------------------------------------------------------------ checking


@dataclass(slots=True)
class Score:
    passed: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    total: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    failures: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str) -> None:
        if name in self.total:
            self.total[name] += 1
            self.passed[name] += int(ok)
        if not ok:
            self.failures.append(detail)


def score(run: CaseRun) -> Score:
    """Compare a run with its case's expectations, field by field."""
    result = Score()
    expect = run.case.expect
    for want in expect.get("memories", []):
        _check_memory(run, want, result)
    for want in expect.get("changed", []):
        _check_changed(run, want, result)
    for want in expect.get("triggers", []):
        _check_trigger(run, want, result)
    _check_diff(run, expect, result)
    _check_entities(run, expect, result)
    for kind, count in (expect.get("counts") or {}).items():
        got = sum(1 for i in run.items if i.kind.value == kind and i.status.value == "active")
        result.check("kind", got == count, f"{got} active {kind} != {count}")
    for text in expect.get("reply_contains", []):
        reply = run.turn.output or ""
        result.check("state", text in reply, f"reply {reply!r} lacks {text!r}")
    for text in expect.get("absent", []):
        result.check("state", text not in run.stored_text, f"{text!r} was stored")
    for text in expect.get("keys_contain", []):
        ok = any(text in key for key in run.keys)
        result.check("state", ok, f"no key contains {text!r}")
    if "intent" in expect:
        intent = next((e.event for e in run.events if e.event.type == "intent"), None)
        got_intent = getattr(intent, "intent", None)
        result.check("kind", got_intent == expect["intent"], f"intent {got_intent}")
    return result


def _touched(run: CaseRun) -> set[uuid.UUID]:
    return {r.target_id for r in run.log if r.target_type is TargetType.ITEM and r.after}


def _find(run: CaseRun, match: str, *, touched_only: bool = True) -> ItemRecord | None:
    touched = _touched(run)
    for item in run.items:
        if touched_only and item.id not in touched:
            continue
        if match in item.title.lower() or match in item.text.lower():
            return item
    return None


def _check_memory(run: CaseRun, want: Mapping[str, Any], result: Score) -> None:
    match = str(want["match"]).lower()
    item = _find(run, match)
    if item is None:
        result.failures.append(f"no memory written matching {match!r}")
        for name in FIELDS:
            key = {"date": "dates", "entity": "entities"}.get(name, name)
            if key in want:
                result.total[name] += 1
        return
    for name in ("kind", "subtype", "state", "modality", "predicate"):
        if name in want:
            got = getattr(item, name)
            got = got.value if hasattr(got, "value") else got
            field_name = "kind" if name in ("subtype", "predicate") else name
            result.check(field_name, got == want[name], f"{match}: {name} {got} != {want[name]}")
    tz = ZoneInfo(run.case.timezone)
    for date in want.get("dates", []):
        got_date = _date(item, str(date["clock"]), tz)
        ok = got_date == (str(date["value"]), date.get("precision"))
        if "rrule" in date:
            ok = ok and item.rrule == date["rrule"]
        detail = f"{match}: {date['clock']} {got_date} {item.rrule} != {dict(date)}"
        result.check("date", ok, detail)
    links = _item_entities(run).get(item.id, set())
    for ent in want.get("entities", []):
        name, _, role = str(ent).partition(":")
        ok = any(n.lower() == name.lower() and (not role or r == role) for n, r in links)
        result.check("entity", ok, f"{match}: entity {ent} not in {sorted(links)}")
    if "category" in want:
        got_category = _category(run, item)
        result.check("category", got_category == want["category"], f"{match}: {got_category}")
    if "layer" in want:
        result.check("layer", item.layer.value == want["layer"], f"{match}: layer {item.layer}")
    if "reconcile" in want:
        reconciles = run.decision.reconciliations if run.decision else []
        got_op = next((r.info.decision.value for r in reconciles if r.label == item.title), None)
        result.check("reconcile", got_op == want["reconcile"], f"{match}: reconcile {got_op}")


def _check_changed(run: CaseRun, want: Mapping[str, Any], result: Score) -> None:
    match = str(want["match"]).lower()
    item = _find(run, match)
    expected = {k: v for k, v in want.items() if k != "match"}
    got = {k: _plain(getattr(item, k)) for k in expected} if item else None
    result.check("state", got == expected, f"changed {match!r}: {got} != {expected}")


def _plain(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _check_trigger(run: CaseRun, want: Mapping[str, Any], result: Score) -> None:
    match = str(want["match"]).lower()
    tz = ZoneInfo(run.case.timezone)
    owners = {i.id for i in run.items if match in i.title.lower() or match in i.text.lower()}
    fires = [
        t.fires_at.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        for t in run.triggers
        if t.item_id in owners and t.fires_at
    ]
    result.check("date", want["fires"] in fires, f"trigger {match!r}: {fires}")


def _check_diff(run: CaseRun, expect: Mapping[str, Any], result: Score) -> None:
    entries = run.diff.entries if run.diff else []
    for rule in expect.get("not_written", []):
        if rule == "no_op":
            ok = any(e.op == "not_written" and e.reconcile is not None for e in entries)
            result.check("reconcile", ok, "no ∅ duplicate (no_op) entry")
        else:
            ok = any(e.op == "not_written" and e.rule_id == rule for e in entries)
            result.check("state", ok, f"no ∅ entry for {rule}")
    for rule in expect.get("held", []):
        ok = any(e.op == "held" and e.rule_id == rule for e in entries)
        result.check("layer", ok, f"no held entry for {rule}")
    for op in expect.get("diff_ops", []):
        result.check("state", any(e.op == op for e in entries), f"no {op} entry in the diff")


def _check_entities(run: CaseRun, expect: Mapping[str, Any], result: Score) -> None:
    created = [e for e in run.entities if e.created_by_turn_id == run.turn.id]
    for want in expect.get("new_entities", []):
        name, _, kind = str(want).partition(":")
        ok = any(
            e.name.lower() == name.lower() and (not kind or e.kind.value == kind) for e in created
        )
        result.check("entity", ok, f"no new entity {want}; new: {[e.name for e in created]}")
    for want in expect.get("updated_entities", []):
        ok = any(
            e.name.lower() == str(want).lower()
            and e.updated_by_turn_id == run.turn.id
            and e.created_by_turn_id != run.turn.id
            for e in run.entities
        )
        result.check("entity", ok, f"entity {want} not updated")
    if "entity_count" in expect:
        live = [e for e in run.entities if e.kind is not EntityKind.SELF and e.status == "active"]
        result.check(
            "entity", len(live) == expect["entity_count"], f"{[e.name for e in live]} entities"
        )
    for want in expect.get("relations", []):
        result.check("entity", want in run.relations, f"relation {want} not in {run.relations}")


def _item_entities(run: CaseRun) -> dict[uuid.UUID, set[tuple[str, str]]]:
    names = {e.id: e.name for e in run.entities}
    out: dict[uuid.UUID, set[tuple[str, str]]] = {}
    for row in run.log:
        if row.target_type is TargetType.ITEM_ENTITY and row.after:
            item_id = uuid.UUID(str(row.after["item_id"]))
            name = names.get(uuid.UUID(str(row.after["entity_id"])), "?")
            out.setdefault(item_id, set()).add((name, str(row.after["role"])))
    for item in run.items:
        if item.subject_entity_id in names:
            out.setdefault(item.id, set()).add((names[item.subject_entity_id], "subject"))
    return out


def _date(item: ItemRecord, clock: str, tz: ZoneInfo) -> tuple[str, str | None]:
    value = {
        "occurred": item.occurred_start,
        "due": item.due_at,
        "valid": item.valid_from,
        "valid_to": item.valid_to,
    }.get(clock)
    precision = item.time_precision.value if item.time_precision else None
    if value is None:
        return ("", precision)
    local = value.astimezone(tz)
    text = {
        "datetime": local.strftime("%Y-%m-%dT%H:%M"),
        "month": local.strftime("%Y-%m"),
        "year": local.strftime("%Y"),
    }.get(precision or "", local.date().isoformat())
    return (text, precision)


def _category(run: CaseRun, item: ItemRecord) -> str | None:
    return run.categories.get(item.category_id) if item.category_id else None


# ------------------------------------------------------------------ report


def report(runs: Sequence[tuple[CaseRun, Score]]) -> str:
    lines = ["field       passed / total"]
    for name in FIELDS:
        passed = sum(s.passed[name] for _, s in runs)
        total = sum(s.total[name] for _, s in runs)
        pct = f"{100 * passed / total:5.1f}%" if total else "   n/a"
        lines.append(f"{name:<11} {passed:>4} / {total:<4} {pct}")
    latencies = sorted(r.latency_ms for r, _ in runs)
    if latencies:
        p95 = latencies[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]
        lines.append(f"latency     p50 {statistics.median(latencies):.0f} ms · p95 {p95} ms")
    cost = sum((r.cost_usd for r, _ in runs), Decimal(0))
    lines.append(
        f"cost        ${cost:.4f} for {len(runs)} cases (${cost / max(len(runs), 1):.5f} each)"
    )
    lines.extend(f"  {run.case.id}: {failure}" for run, s in runs for failure in s.failures)
    return "\n".join(lines)
