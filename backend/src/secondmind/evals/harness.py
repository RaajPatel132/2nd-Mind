"""The eval harness (R.1, R.2): one way to run a suite on the fake provider, as a dry run, or
live, with budgets, the response cache, case selection and a stamped run file.

A **live** run:

1. refuses to start when the sprint total plus its budget would pass ``LIVE_TOTAL_BUDGET_USD``,
   or its batch has used its allowance (:mod:`secondmind.evals.spend`);
2. refuses a routing with a model priced at or above Claude Sonnet 5 unless ``ALLOW_EXPENSIVE=1``
   and the cases are a named subset (``--cases @reference``);
3. runs the same cases as a dry run first (the fake provider, each call costed as the live model
   it stands in for) and refuses when that estimate passes its budget;
4. runs case by case, and stops cleanly (``stopped: budget``) before a case whose estimate would
   take it past its budget;
5. writes the run file, adds its spend to the running total, and prints both.

Routings come from ``config/models.yaml`` with an optional overlay from ``evals/routings/`` (the
candidates of R.4), applied as ``MODEL_<STEP>`` overrides, so the run records exactly what a
deployment with those variables would run.
"""

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from secondmind.config import (
    DEFAULT_RESOURCES_DIR,
    STEP_KINDS,
    ModelRef,
    PriceTable,
    PromptRegistry,
    Routing,
    RoutingFile,
    Step,
    StepKind,
    compute_config_hash,
    read_price_table,
    read_routing_file,
    resolve_routing,
)
from secondmind.core import ConfigError
from secondmind.evals.calls import (
    CACHE_DIR_NAME,
    CallLog,
    MeteredAdapter,
    ResponseCache,
)
from secondmind.evals.calls import Estimator as CostEstimator
from secondmind.evals.runs import CaseRecord, Mode, RunRecord, git_state, new_run_id, write_run
from secondmind.evals.select import Selection
from secondmind.evals.spend import RUNS_DIR, BudgetRefusedError, Budgets, RunMeter, SpendBook
from secondmind.providers import FakeProvider, FakeScript, ModelRouter, ResiliencePolicy
from secondmind.providers.adapters import build_adapter

ROUTINGS_DIR = DEFAULT_RESOURCES_DIR / "evals" / "routings"
CONFIGURED = "configured"
# Models priced at or above this one need ALLOW_EXPENSIVE=1 and a named subset.
EXPENSIVE_FROM = ModelRef(provider="anthropic", model="claude-sonnet-5")


def blended(prices: PriceTable, ref: ModelRef) -> Decimal:
    p = prices.price_for(ref)
    return 3 * p.input + p.output


def routing_overlay(name: str, directory: Path = ROUTINGS_DIR) -> dict[str, str]:
    """``MODEL_<STEP>`` overrides for a named candidate routing (empty for ``configured``)."""
    if name == CONFIGURED:
        return {}
    path = directory / f"{name}.yaml"
    if not path.exists():
        known = sorted(p.stem for p in directory.glob("*.yaml"))
        raise ConfigError(f"no routing {name!r}; known: {', '.join([CONFIGURED, *known])}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    env: dict[str, str] = {}
    default_fallback = raw.get("fallback")
    fallbacks = raw.get("fallbacks") or {}
    for step, ref in (raw.get("steps") or {}).items():
        env[f"MODEL_{str(step).upper()}"] = str(ref)
    for step in Step:
        if step.value in fallbacks:
            env[f"MODEL_{step.value.upper()}_FALLBACK"] = str(fallbacks[step.value])
        elif default_fallback is not None and STEP_KINDS[step] is StepKind.CHAT:
            env[f"MODEL_{step.value.upper()}_FALLBACK"] = str(default_fallback)
    return env


def _with_placeholder_keys(file: RoutingFile, environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` with a stand-in for every missing provider key, so the routing a live run
    *would* use can be resolved (and priced) without keys, for a dry run."""
    env = dict(environ)
    for provider in file.providers.values():
        if provider.api_key_env and not env.get(provider.api_key_env, "").strip():
            env[provider.api_key_env] = "dry-run-placeholder"
    return env


@dataclass(slots=True)
class Pass:
    """One pass over the cases (the dry-run estimate, or the run itself): how its routers are
    built and where its calls are logged."""

    mode: Mode
    harness: "Harness"
    log: CallLog = field(default_factory=CallLog)
    # Whatever a suite keeps for the pass (a seeded workspace, its router).
    state: dict[str, Any] = field(default_factory=dict)
    _live: ModelRouter | None = None
    _routers: list[ModelRouter] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.mode == "live"

    def router(self, script: FakeScript | None = None) -> ModelRouter:
        """The live router (one per pass), or a fake one replaying ``script``."""
        h = self.harness
        if self.live:
            if self._live is None:
                self._live = h.build_live_router(self.log)
                self._routers.append(self._live)
            return self._live
        router = h.build_replay_router(self.log, script or FakeScript(), dry=self.mode == "dry-run")
        self._routers.append(router)
        return router

    async def aclose(self) -> None:
        for router in self._routers:
            await router.aclose()
        self._routers.clear()
        self._live = None

    def record(
        self,
        *,
        mark: int,
        case_id: str,
        title: str = "",
        tags: Sequence[str] = (),
        passed: bool,
        failures: Sequence[str] = (),
        scores: Mapping[str, Any] | None = None,
        latency_ms: int | None = None,
        ttft_ms: int | None = None,
        detail: Mapping[str, Any] | None = None,
        timed_from: int | None = None,
    ) -> CaseRecord:
        """The case's record from the calls logged since ``mark``. Calls from ``timed_from`` on
        (default: all) are the ones its latency measured."""
        calls = self.log.since(mark)
        first_timed = (timed_from if timed_from is not None else mark) - mark
        return CaseRecord(
            id=case_id,
            title=title,
            tags=list(tags),
            passed=passed,
            failures=list(failures),
            scores=dict(scores or {}),
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            calls=[{**c.to_json(), "timed": i >= first_timed} for i, c in enumerate(calls)],
            cost_usd=float(sum((c.cost_usd for c in calls), Decimal(0))),
            spent_usd=float(sum((c.spent_usd for c in calls), Decimal(0))),
            cache_hits=sum(c.cache_hit for c in calls),
            detail=dict(detail or {}),
        )


# One case: given the pass, produce its record.
CaseRunner = Callable[[Any, Pass], Awaitable[CaseRecord]]


class Harness:
    def __init__(
        self,
        *,
        suite: str,
        mode: Mode,
        routing: str = CONFIGURED,
        selection: Selection | None = None,
        cache: bool = True,
        budgets: Budgets | None = None,
        environ: Mapping[str, str] | None = None,
        resources: Path = DEFAULT_RESOURCES_DIR,
        runs_dir: Path = RUNS_DIR,
        fixture: Mapping[str, str] | None = None,
        note: str | None = None,
        out: Any = sys.stderr,
    ) -> None:
        self.suite = suite
        self.mode = mode
        self.routing_name = routing
        self.selection = selection or Selection()
        self.cache_reads = cache
        self.budgets = budgets or Budgets.from_env(environ)
        self.resources = resources
        self.runs_dir = runs_dir
        self.fixture = dict(fixture or {})
        self.note = note
        self.out = out
        base = dict(os.environ if environ is None else environ)
        self.file = read_routing_file(resources / "config" / "models.yaml")
        self.env = {**base, **routing_overlay(routing, resources / "evals" / "routings")}
        self.prices = read_price_table(resources / "config" / "prices.yaml")
        self.prompts = PromptRegistry.load(resources / "prompts")
        # What a live run would use; a live run resolves it again with the real keys.
        self.target: Routing = resolve_routing(
            self.file, _with_placeholder_keys(self.file, self.env), "live"
        )
        self.prices.require(self.target.refs())
        self.cache = ResponseCache(runs_dir / CACHE_DIR_NAME, read=cache)

    # ------------------------------------------------------------------ routers

    def build_live_router(self, log: CallLog) -> ModelRouter:
        routing = resolve_routing(self.file, self.env, "live")
        used = sorted({ref.provider for ref in routing.refs()})
        adapters = {
            name: MeteredAdapter(
                build_adapter(routing.providers[name], self.env),
                log=log,
                prices=self.prices,
                cache=self.cache,
                billed=True,
            )
            for name in used
        }
        return ModelRouter(
            routing=routing,
            prices=self.prices,
            adapters=adapters,
            policy=ResiliencePolicy(max_retries=2),
        )

    def build_replay_router(self, log: CallLog, script: FakeScript, *, dry: bool) -> ModelRouter:
        routing = resolve_routing(self.file, {}, "fake")
        targets = {step.value: r.primary for step, r in self.target.routes.items()} if dry else None
        estimator = CostEstimator(self.prices, self.cache.output_stats()) if dry else None
        fake = MeteredAdapter(
            FakeProvider("fake", script=script),
            log=log,
            prices=self.prices,
            targets=targets,
            estimator=estimator,
        )
        return ModelRouter(
            routing=routing,
            prices=self.prices,
            adapters={"fake": fake},
            policy=ResiliencePolicy(max_retries=0),
        )

    # ------------------------------------------------------------------ gates

    def expensive_models(self) -> list[str]:
        limit = blended(self.prices, EXPENSIVE_FROM)
        refs = set()
        for route in self.target.routes.values():
            if route.kind is not StepKind.CHAT:
                continue
            refs.add(route.primary)
            if route.fallback is not None:
                refs.add(route.fallback)
        return sorted(str(r) for r in refs if blended(self.prices, r) >= limit)

    def check_expensive(self) -> None:
        expensive = self.expensive_models()
        if not expensive or self.mode != "live":
            return
        if not self.budgets.allow_expensive:
            raise BudgetRefusedError(
                f"this routing uses models priced at or above {EXPENSIVE_FROM}: "
                f"{', '.join(expensive)}. Set ALLOW_EXPENSIVE=1 and name a subset "
                "(--cases @reference) to run it."
            )
        named = self.selection.subsets
        if not named or len(named) != len(self.selection.cases):
            raise BudgetRefusedError(
                "an expensive routing runs only on a named subset (--cases @reference), "
                "never on a whole suite"
            )

    # ------------------------------------------------------------------ running

    def _stamp(self, mode: Mode) -> RunRecord:
        sha, dirty = git_state(self.resources)
        routing = self.target
        return RunRecord(
            run_id=new_run_id(sha),
            suite=self.suite,
            mode=mode,
            routing=self.routing_name,
            started_at=datetime.now(UTC),
            git_sha=sha,
            git_dirty=dirty,
            config_hash=compute_config_hash(routing, self.prices, self.prompts),
            price_version=self.prices.version,
            prompt_versions={s.value: r.prompt for s, r in routing.routes.items()},
            models={s.value: str(r.primary) for s, r in routing.routes.items()},
            fallbacks={
                s.value: (str(r.fallback) if r.fallback else None)
                for s, r in routing.routes.items()
            },
            fixture=self.fixture,
            selection=self.selection.describe(),
            batch=self.budgets.batch if mode == "live" else None,
            note=self.note,
        )

    def say(self, text: str) -> None:
        self.out.write(text + "\n")
        self.out.flush()

    async def run(
        self,
        cases: Sequence[Any],
        run_case: CaseRunner,
        *,
        id_of: Callable[[Any], str],
        setup: Callable[[Pass], Awaitable[None]] | None = None,
    ) -> RunRecord:
        """Run ``cases`` in this harness's mode and write the run file."""
        if not cases:
            raise BudgetRefusedError("no cases selected")
        book = SpendBook.load(self.runs_dir / ".spend")
        budget: Decimal | None = None
        estimates: dict[str, Decimal] = {}
        if self.mode == "live":
            budget = book.effective_budget(self.budgets)
            self.check_expensive()
            self.say(f"estimating on the fake provider first ({len(cases)} cases)…")
            estimate = await self._pass("dry-run", cases, run_case, id_of=id_of, setup=setup)
            estimates = {c.id: Decimal(str(c.cost_usd)) for c in estimate.cases}
            total = sum(estimates.values(), Decimal(0)) + Decimal(str(estimate.setup_cost_usd))
            self.say(f"estimated ${total:.4f} for {len(cases)} cases; budget ${budget:.4f}")
            if total > budget:
                raise BudgetRefusedError(
                    f"the estimate ${total:.4f} passes this run's budget ${budget:.4f}; "
                    "select fewer cases or raise LIVE_RUN_BUDGET_USD within the batch allowance"
                )
        record = await self._pass(
            self.mode,
            cases,
            run_case,
            id_of=id_of,
            setup=setup,
            budget=budget,
            estimates=estimates,
        )
        if self.mode == "live":
            record.budget_usd = float(budget) if budget is not None else None
            record.estimate_usd = float(sum(estimates.values(), Decimal(0)))
        path = write_run(record, self.runs_dir)
        self.say(f"run {record.run_id} ({record.status}) -> {path}")
        if self.mode == "live":
            book.record(
                run_id=record.run_id,
                suite=self.suite,
                spent_by_provider=spent_by_provider(record),
                budget=budget or Decimal(0),
                batch=self.budgets.batch,
            )
            self.say(f"this run spent ${record.spent_usd:.4f}; {book.summary(self.budgets.total)}")
        elif self.mode == "dry-run":
            self.say(f"estimated live cost ${record.cost_usd:.4f} for {len(record.cases)} cases")
        return record

    async def _pass(
        self,
        mode: Mode,
        cases: Sequence[Any],
        run_case: CaseRunner,
        *,
        id_of: Callable[[Any], str],
        setup: Callable[[Pass], Awaitable[None]] | None,
        budget: Decimal | None = None,
        estimates: Mapping[str, Decimal] | None = None,
    ) -> RunRecord:
        """One pass. Never raises for a case: a crash is a failed case, and an interrupt ends the
        pass as ``stopped: error`` with what it has, so its spend is still recorded."""
        record = self._stamp(mode)
        run = Pass(mode=mode, harness=self)
        meter = RunMeter(budget=budget or Decimal(0))
        try:
            if setup is not None:
                mark = run.log.mark()
                try:
                    await setup(run)
                finally:
                    calls = run.log.since(mark)
                    record.setup_calls = [c.to_json() for c in calls]
                    for call in calls:
                        meter.add(call.provider, call.spent_usd)
            for case in cases:
                case_id = id_of(case)
                if budget is not None:
                    done = len(record.cases)
                    average = meter.spent / done if done else Decimal(0)
                    guess = max((estimates or {}).get(case_id, Decimal(0)), average)
                    if not meter.can_afford(guess):
                        record.status = "stopped: budget"
                        self.say(
                            f"stopping before {case_id}: ${meter.spent:.4f} spent, the next case "
                            f"(~${guess:.4f}) would pass ${budget:.4f}"
                        )
                        break
                started = time.perf_counter()
                mark = run.log.mark()
                try:
                    result = await run_case(case, run)
                except Exception as exc:
                    result = run.record(
                        mark=mark,
                        case_id=case_id,
                        passed=False,
                        failures=[f"crashed: {type(exc).__name__}: {exc}"[:500]],
                    )
                for call in run.log.since(mark):
                    meter.add(call.provider, call.spent_usd)
                record.cases.append(result)
                took = round((time.perf_counter() - started) * 1000)
                flag = "ok  " if result.passed else "FAIL"
                spent = f" spent ${result.spent_usd:.4f}" if result.spent_usd else ""
                hits = f" ({result.cache_hits} cached)" if result.cache_hits else ""
                self.say(f"  {flag} {case_id}: {took} ms, ${result.cost_usd:.4f}{spent}{hits}")
                broke = sorted(
                    {str(c["provider"]) for c in result.calls if c.get("failed") == "credit"}
                )
                if broke:
                    record.status = "stopped: error"
                    record.note = f"out of provider credit: {', '.join(broke)}"
                    self.say(f"stopping: {record.note}")
                    break
        except (KeyboardInterrupt, asyncio.CancelledError, Exception) as exc:
            record.status = "stopped: error"
            record.note = f"{record.note + '; ' if record.note else ''}{type(exc).__name__}: {exc}"
            self.say(f"stopped: {type(exc).__name__}: {exc}")
        finally:
            await run.aclose()
        record.finished_at = datetime.now(UTC)
        return record


def spent_by_provider(record: RunRecord) -> dict[str, Decimal]:
    """Real spend of a run per provider: every call that wasn't a cache hit or an estimate."""
    spent: dict[str, Decimal] = {}
    calls = [*record.setup_calls, *(c for case in record.cases for c in case.calls)]
    for call in calls:
        if call.get("billed") and not call.get("cache_hit"):
            provider = str(call["provider"])
            spent[provider] = spent.get(provider, Decimal(0)) + Decimal(str(call["cost_usd"]))
    return spent
