"""R.1 and R.2: the live harness's guards, run offline. Budgets, the stop rule, the response
cache, the expensive-model gate, case selection, stamped run files, and the wiring that keeps
keys and provider calls out of every non-live target."""

import os
import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from secondmind.config import DEFAULT_RESOURCES_DIR, load_app_config, read_price_table
from secondmind.evals.calls import CallLog, MeteredAdapter, ResponseCache
from secondmind.evals.harness import Harness, Pass, routing_overlay
from secondmind.evals.intent import load_cases as intent_cases
from secondmind.evals.intent import run_intent_case
from secondmind.evals.runs import CaseRecord, RunRecord, load_run, metrics, write_run
from secondmind.evals.select import Selection, SelectionError, select
from secondmind.evals.spend import BudgetRefusedError, Budgets, SpendBook
from secondmind.providers import (
    AdapterRequest,
    ChatMessage,
    FakeOutcome,
    FakeProvider,
    ProviderError,
    ProviderErrorKind,
)
from secondmind.providers.adapters import AnthropicAdapter, build_router
from tests.conftest import ProviderCallBlockedError

PRICES = read_price_table(DEFAULT_RESOURCES_DIR / "config" / "prices.yaml")
REPO = DEFAULT_RESOURCES_DIR.parent
ALLOWANCES = {"B1": Decimal("0.10")}


def _spent(book: SpendBook, amount: str, batch: str | None = None) -> None:
    book.record(
        run_id="r",
        suite="ingest",
        spent_by_provider={"openai": Decimal(amount)},
        budget=Decimal("0.50"),
        batch=batch,
        allowances=ALLOWANCES,
    )


# ------------------------------------------------------------------ the sprint budget


def test_a_run_is_refused_when_it_would_pass_the_sprint_budget(tmp_path: Path) -> None:
    book = SpendBook.load(tmp_path / ".spend")
    _spent(book, "3.60")
    with pytest.raises(BudgetRefusedError, match="sprint budget"):
        book.effective_budget(Budgets(run=Decimal("0.50"), total=Decimal("4")))
    assert book.effective_budget(Budgets(run=Decimal("0.40"), total=Decimal("4"))) == Decimal(
        "0.40"
    )


def test_the_total_adds_up_per_provider_and_survives_a_reload(tmp_path: Path) -> None:
    book = SpendBook.load(tmp_path / ".spend")
    book.record(
        run_id="a",
        suite="recall",
        spent_by_provider={"openai": Decimal("0.01"), "anthropic": Decimal("0.02")},
        budget=Decimal("0.5"),
        batch=None,
    )
    _spent(book, "0.005")
    again = SpendBook.load(tmp_path / ".spend")
    assert again.total == Decimal("0.035")
    assert again.by_provider == {"openai": Decimal("0.015"), "anthropic": Decimal("0.02")}
    assert "left" in again.summary(Decimal("4"))


def test_a_batch_gets_no_more_than_its_allowance(tmp_path: Path) -> None:
    book = SpendBook.load(tmp_path / ".spend")
    _spent(book, "0.07", batch="B1")
    budget = book.effective_budget(Budgets(run=Decimal("0.50"), batch="B1"), allowances=ALLOWANCES)
    assert budget == Decimal("0.03")


def test_a_batch_over_125_percent_halts_every_later_run(tmp_path: Path) -> None:
    book = SpendBook.load(tmp_path / ".spend")
    _spent(book, "0.13", batch="B1")
    assert book.halted is not None
    with pytest.raises(BudgetRefusedError, match="halted"):
        SpendBook.load(tmp_path / ".spend").effective_budget(Budgets(), allowances=ALLOWANCES)
    book.clear_halt()
    SpendBook.load(tmp_path / ".spend").effective_budget(Budgets(), allowances=ALLOWANCES)


def test_budgets_come_from_the_environment() -> None:
    budgets = Budgets.from_env(
        {"LIVE_RUN_BUDGET_USD": "0.35", "LIVE_TOTAL_BUDGET_USD": "3", "LIVE_BATCH": "B2"}
    )
    assert budgets == Budgets(run=Decimal("0.35"), total=Decimal(3), batch="B2")
    assert Budgets.from_env({}).run == Decimal("0.50")
    assert Budgets.from_env({"ALLOW_EXPENSIVE": "1"}).allow_expensive


def test_the_sprint_total_defaults_to_this_sprints_share() -> None:
    assert Budgets.from_env({}).total == Decimal("1.50")


def test_the_spending_plan_adds_up_to_the_sprint_total() -> None:
    from secondmind.evals.spend import read_allowances  # noqa: PLC0415

    assert sum(read_allowances().values()) == Decimal("1.50")


# ------------------------------------------------------------------ the response cache


class Capital(BaseModel):
    capital: str


def _request(text: str = "capital of France?") -> AdapterRequest:
    return AdapterRequest(
        step="intent",
        model="gpt-6-luna",
        system="be brief",
        messages=[ChatMessage.user(text)],
        max_output_tokens=64,
    )


def _live_fake(log: CallLog, cache: ResponseCache | None) -> tuple[MeteredAdapter, FakeProvider]:
    fake = FakeProvider("openai")
    fake.script.add(FakeOutcome(structured={"capital": "Paris"}))
    return MeteredAdapter(fake, log=log, prices=PRICES, cache=cache, billed=True), fake


async def test_a_repeated_request_is_a_cache_hit_that_costs_nothing(tmp_path: Path) -> None:
    log = CallLog()
    adapter, fake = _live_fake(log, ResponseCache(tmp_path))
    first = await adapter.structured(_request(), Capital)
    second = await adapter.structured(_request(), Capital)
    assert first.value == second.value
    assert len(fake.requests) == 1
    miss, hit = log.records
    assert not miss.cache_hit
    assert miss.spent_usd > 0
    assert hit.cache_hit
    assert hit.spent_usd == 0
    assert hit.cost_usd == miss.cost_usd


async def test_a_changed_request_misses_and_no_cache_forces_fresh_calls(tmp_path: Path) -> None:
    log = CallLog()
    adapter, _ = _live_fake(log, ResponseCache(tmp_path))
    await adapter.structured(_request(), Capital)
    await adapter.structured(_request("capital of Peru?"), Capital)
    fresh, _ = _live_fake(log, ResponseCache(tmp_path, read=False))
    await fresh.structured(_request(), Capital)
    assert [r.cache_hit for r in log.records] == [False, False, False]


async def test_a_billed_failure_is_costed_by_estimate(tmp_path: Path) -> None:
    log = CallLog()
    fake = FakeProvider("openai")
    fake.script.add(FakeOutcome(structured={"not": "valid"}))
    adapter = MeteredAdapter(
        fake, log=log, prices=PRICES, cache=ResponseCache(tmp_path), billed=True
    )
    with pytest.raises(ProviderError):
        await adapter.structured(_request(), Capital)
    assert log.records[0].failed == ProviderErrorKind.INVALID_OUTPUT.value
    assert log.records[0].spent_usd > 0


def test_the_apps_own_router_never_goes_through_the_harness_cache(
    base_env: dict[str, str],
) -> None:
    env = {**base_env, "OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-test"}
    router = build_router(load_app_config(env), env)
    adapters = router._adapters
    assert adapters
    assert not any(isinstance(a, MeteredAdapter) for a in adapters.values())


# ------------------------------------------------------------------ the harness


def _harness(tmp_path: Path, **kwargs: Any) -> Harness:
    return Harness(
        suite="intent",
        mode=kwargs.pop("mode", "live"),
        routing=kwargs.pop("routing", "economy-b"),
        runs_dir=tmp_path,
        environ={"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-test"},
        out=open(os.devnull, "w"),
        **kwargs,
    )


def _billed_fake(harness: Harness) -> None:
    """Make the harness's "live" router a billed fake, so a live run can be tested offline."""

    def build(log: CallLog) -> Any:
        from secondmind.ingestion import offline_responders  # noqa: PLC0415
        from secondmind.providers import FakeScript, ModelRouter, ResiliencePolicy  # noqa: PLC0415

        routing = harness.target
        fakes = {
            name: MeteredAdapter(
                FakeProvider(name, script=FakeScript(responders=offline_responders())),
                log=log,
                prices=harness.prices,
                billed=True,
            )
            for name in sorted({r.provider for r in routing.refs()})
        }
        return ModelRouter(
            routing=routing, prices=harness.prices, adapters=fakes, policy=ResiliencePolicy()
        )

    harness.build_live_router = build  # type: ignore[method-assign]


async def test_a_live_run_stops_cleanly_at_its_budget(tmp_path: Path) -> None:
    harness = _harness(tmp_path, budgets=Budgets(run=Decimal("0.00012")))
    _billed_fake(harness)
    real_pass = harness._pass

    async def pass_with_free_estimate(mode: Any, *args: Any, **kwargs: Any) -> RunRecord:
        record = await real_pass(mode, *args, **kwargs)
        if mode == "dry-run":  # an estimate that says "cheap": the in-run guard must catch it
            for case in record.cases:
                case.cost_usd = 0.0
        return record

    harness._pass = pass_with_free_estimate  # type: ignore[method-assign]
    cases = intent_cases()
    record = await harness.run(cases, run_intent_case, id_of=lambda c: c.id)
    assert record.status == "stopped: budget"
    assert 0 < len(record.cases) < len(cases)
    assert record.spent_usd <= 0.00012
    book = SpendBook.load(tmp_path / ".spend")
    assert book.total > 0
    assert (tmp_path / "intent" / f"{record.run_id}.json").exists()


async def test_a_live_run_whose_estimate_passes_its_budget_does_not_start(tmp_path: Path) -> None:
    harness = _harness(tmp_path, budgets=Budgets(run=Decimal("0.000001")))
    _billed_fake(harness)
    with pytest.raises(BudgetRefusedError, match="estimate"):
        await harness.run(intent_cases(), run_intent_case, id_of=lambda c: c.id)
    assert not SpendBook.load(tmp_path / ".spend").runs


async def test_a_dry_run_estimates_the_live_model_and_spends_nothing(tmp_path: Path) -> None:
    harness = _harness(tmp_path, mode="dry-run")
    record = await harness.run(intent_cases()[:3], run_intent_case, id_of=lambda c: c.id)
    assert record.cost_usd > 0
    assert record.spent_usd == 0
    assert {c["model"] for case in record.cases for c in case.calls} == {"gpt-6-luna"}
    assert (tmp_path / "intent" / f"{record.run_id}.fake.json").exists()


async def test_a_crashed_case_is_a_failed_case_not_a_lost_run(tmp_path: Path) -> None:
    harness = _harness(tmp_path, mode="fake")

    async def boom(case: Any, run: Pass) -> CaseRecord:
        raise RuntimeError("no")

    record = await harness.run(intent_cases()[:2], boom, id_of=lambda c: c.id)
    assert record.status == "complete"
    assert [c.passed for c in record.cases] == [False, False]
    assert "RuntimeError" in record.cases[0].failures[0]


def test_expensive_models_need_the_flag_and_a_named_subset(tmp_path: Path) -> None:
    with pytest.raises(BudgetRefusedError, match="ALLOW_EXPENSIVE"):
        _harness(tmp_path, routing="reference").check_expensive()
    flagged = Budgets(allow_expensive=True)
    with pytest.raises(BudgetRefusedError, match="named subset"):
        _harness(tmp_path, routing="reference", budgets=flagged).check_expensive()
    _harness(
        tmp_path,
        routing="reference",
        budgets=flagged,
        selection=Selection(cases=("@reference",)),
    ).check_expensive()
    _harness(tmp_path, routing="economy-b").check_expensive()
    # The configured routing is the economy one: nothing on it needs the flag.
    assert _harness(tmp_path, routing="configured").expensive_models() == []


def test_a_candidate_routing_is_a_set_of_model_overrides() -> None:
    env = routing_overlay("economy-b")
    assert env["MODEL_EXTRACT"] == "anthropic:claude-haiku-4-5"
    assert env["MODEL_PLAN"] == "openai:gpt-6-luna"
    assert env["MODEL_ANSWER_FALLBACK"] == "none"
    assert "MODEL_EMBED_FALLBACK" not in env
    assert routing_overlay("configured") == {}


# ------------------------------------------------------------------ selection


CASES = [
    {"id": "01-a", "tags": ["exact"]},
    {"id": "02-b", "tags": ["exact", "abstain"]},
    {"id": "03-c", "tags": ["list"]},
    {"id": "04-d", "tags": ["count"]},
    {"id": "05-e", "tags": ["count"]},
]


def _select(selection: Selection, subsets: dict[str, list[str]] | None = None) -> list[str]:
    return [
        c["id"]
        for c in select(
            CASES,
            selection,
            suite="recall",
            id_of=lambda c: c["id"],
            tags_of=lambda c: c["tags"],
            stratum_of=lambda c: c["tags"][0],
            subsets=subsets or {"probe": ["01", "count"]},
        )
    ]


def test_cases_are_picked_by_id_prefix_tag_and_subset() -> None:
    assert _select(Selection(cases=("03",))) == ["03-c"]
    assert _select(Selection(cases=("abstain", "01-a"))) == ["01-a", "02-b"]
    assert _select(Selection(cases=("@probe",))) == ["01-a", "04-d", "05-e"]
    with pytest.raises(SelectionError):
        _select(Selection(cases=("nope",)))


def test_a_stratified_sample_covers_every_stratum_and_is_reproducible() -> None:
    picked = _select(Selection(sample=3, stratify="shape"))
    assert len(picked) == 3
    assert {c["tags"][0] for c in CASES if c["id"] in picked} == {"exact", "list", "count"}
    assert picked == _select(Selection(sample=3, stratify="shape"))


def test_only_failed_takes_the_failures_of_an_earlier_run() -> None:
    assert _select(Selection(only_failed=("02-b", "05-e"))) == ["02-b", "05-e"]


# ------------------------------------------------------------------ run files


def _run(**kwargs: Any) -> RunRecord:
    return RunRecord.model_validate(
        {
            "run_id": "20260928T000000Z-abcdef0",
            "suite": "recall",
            "mode": "live",
            "routing": "economy-b",
            "started_at": "2026-09-28T00:00:00Z",
            "git_sha": "abcdef0",
            "git_dirty": False,
            "config_hash": "0" * 64,
            "price_version": "v",
            "prompt_versions": {},
            "models": {},
            "fallbacks": {},
            **kwargs,
        }
    )


def _case(case_id: str, latency: int, *, hit: bool = False) -> CaseRecord:
    call = {"step": "answer", "provider": "openai", "model": "m", "cost_usd": 0.001,
            "cache_hit": hit, "billed": True}  # fmt: skip
    return CaseRecord(id=case_id, passed=True, latency_ms=latency, calls=[call], cost_usd=0.001)


def test_a_run_file_with_anything_like_a_key_is_refused(tmp_path: Path) -> None:
    leaky = _run(note="key sk-ant-api03-abcdefghijklmnop")
    with pytest.raises(ValueError, match="key"):
        write_run(leaky, tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_live_runs_are_committed_and_fake_runs_are_not(tmp_path: Path) -> None:
    assert write_run(_run(), tmp_path).name.endswith("-abcdef0.json")
    assert write_run(_run(mode="fake"), tmp_path).name.endswith(".fake.json")
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "backend/evals/runs/**/*.fake.json" in ignored
    assert "backend/evals/runs/.spend" in ignored
    assert "backend/evals/runs/.cache/" in ignored


def test_latency_comes_from_stored_cases_and_leaves_out_cache_hits(tmp_path: Path) -> None:
    run = _run(cases=[_case("a", 100), _case("b", 300), _case("c", 5, hit=True)])
    path = write_run(run, tmp_path)
    loaded = load_run(path)
    got = {m.label: m.value for m in metrics(loaded)}
    assert got["cases timed (no cache hits)"] == 2
    assert got["total p50"] in (100, 300)
    assert got["total p95"] == 300


def test_committed_run_files_carry_no_keys() -> None:
    from secondmind.evals.runs import committed_run_files, looks_like_secret  # noqa: PLC0415

    for path in committed_run_files():
        assert looks_like_secret(path.read_text(encoding="utf-8")) is None, path


# ------------------------------------------------------------------ keys reach live paths only


def _recipe(target: str) -> str:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(target)}:.*\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert match, target
    return match.group(1)


def test_only_live_targets_load_the_env_file() -> None:
    for target in ("test", "test-int", "e2e", "eval-ingest", "eval-recall", "bench-vectors"):
        assert "LIVE_RUN" not in _recipe(target), target
    for target in ("test-live", "live-check", "eval-ingest-live", "eval-recall-live",
                   "eval-intent-live", "bench-vectors-live"):  # fmt: skip
        assert "LIVE_RUN" in _recipe(target), target
    assert '"integration and not live"' in _recipe("test-int")


def test_the_env_loader_exports_env_and_forces_live_mode(tmp_path: Path) -> None:
    if not shutil.which("bash"):
        pytest.skip("needs bash")
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "live-env.sh", tmp_path / "scripts" / "live-env.sh")
    (tmp_path / ".env").write_text(
        "# comment\nOPENAI_API_KEY=sk-from-file\nMODEL_PROVIDER_MODE=auto\n"
        'QUOTED="a value"\nALREADY=file\n$(touch pwned)=x\n',
        encoding="utf-8",
    )
    out = subprocess.run(  # noqa: S603
        [str(tmp_path / "scripts" / "live-env.sh"), "env"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "ALREADY": "shell"},
        cwd=tmp_path,
    ).stdout.splitlines()
    assert "OPENAI_API_KEY=sk-from-file" in out
    assert "MODEL_PROVIDER_MODE=live" in out
    assert "QUOTED=a value" in out
    assert "ALREADY=shell" in out
    assert not (tmp_path / "pwned").exists()


async def test_a_non_live_test_cannot_reach_a_provider() -> None:
    adapter = AnthropicAdapter("anthropic", api_key="sk-ant-test-key")
    with pytest.raises(ProviderError) as info:
        await adapter.chat(
            AdapterRequest(
                step="intent",
                model="claude-haiku-4-5",
                system=None,
                messages=[ChatMessage.user("hi")],
                max_output_tokens=1,
            )
        )
    chain: list[BaseException] = []
    err: BaseException | None = info.value
    while err is not None:
        chain.append(err)
        err = err.__cause__ or err.__context__
    assert any(isinstance(e, ProviderCallBlockedError) for e in chain)
    await adapter.aclose()
