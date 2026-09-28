"""``make live-check`` (R.1): does every model we route to answer, and is everything it needs in
place? Before any live batch, for well under a cent.

For every routed step (primary and fallback) and every picker model it sends one tiny request
(one output token; a little more for models that reason before answering, which need room to
say anything) and prints provider, model, latency and pass/fail. It also checks that every
model has a price and every step's prompt is released in the prompt lock. Keys are shown by
their last four characters only. What it spent goes on the live spend total.
"""

import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TextIO

from secondmind.config import (
    DEFAULT_RESOURCES_DIR,
    ModelRef,
    PromptRegistry,
    Routing,
    StepKind,
    read_lock,
    read_price_table,
    read_routing_file,
    resolve_routing,
)
from secondmind.core import ConfigError, usd
from secondmind.evals.spend import BudgetRefusedError, Budgets, SpendBook
from secondmind.providers import AdapterRequest, ChatMessage, ProviderAdapter, ProviderError
from secondmind.providers.adapters import build_adapter

PING = "Reply with the word OK."


def mask(key: str) -> str:
    """``sk-…abcd``: enough to tell keys apart, nothing to use."""
    key = key.strip()
    if not key:
        return "(not set)"
    prefix = key.split("-", 1)[0] + "-" if "-" in key[:6] else ""
    return f"{prefix}…{key[-4:]}"


def _output_cap(ref: ModelRef) -> int:
    reasons = ref.provider == "openai" and ref.model.startswith(("gpt-5", "gpt-6", "o"))
    return 16 if reasons else 1


@dataclass(frozen=True, slots=True)
class Row:
    what: str
    ref: ModelRef
    ok: bool
    latency_ms: int
    detail: str
    cost: Decimal


async def _ping(adapter: ProviderAdapter, ref: ModelRef, kind: StepKind) -> tuple[Decimal, str]:
    prices = read_price_table(DEFAULT_RESOURCES_DIR / "config" / "prices.yaml")
    if kind is StepKind.EMBEDDING:
        result = await adapter.embed(ref.model, ["ping"])
        usage = result.usage
        detail = f"{len(result.vectors[0])} dimensions"
    else:
        reply = await adapter.chat(
            AdapterRequest(
                step="ping",
                model=ref.model,
                system=None,
                messages=[ChatMessage.user(PING)],
                max_output_tokens=_output_cap(ref),
            )
        )
        usage = reply.usage
        detail = f"{usage.input_tokens} in / {usage.output_tokens} out, stop {reply.stop_reason}"
    cost = prices.cost(
        ref,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
    )
    return cost, detail


Targets = dict[ModelRef, tuple[list[str], StepKind]]


def _targets(routing: Routing) -> Targets:
    """Every routed model (primary and fallback) and every picker model, once."""
    targets: Targets = {}
    for step, route in routing.routes.items():
        for role, ref in (("", route.primary), (" fallback", route.fallback)):
            if ref is not None:
                targets.setdefault(ref, ([], route.kind))[0].append(f"{step.value}{role}")
    for choice in routing.choices:
        targets.setdefault(choice.ref, ([], StepKind.CHAT))[0].append("picker")
    return targets


def _problems(routing: Routing, targets: Targets) -> list[str]:
    prices = read_price_table(DEFAULT_RESOURCES_DIR / "config" / "prices.yaml")
    prompts = PromptRegistry.load(DEFAULT_RESOURCES_DIR / "prompts")
    lock = read_lock(DEFAULT_RESOURCES_DIR / "prompts")
    problems: list[str] = []
    for step, route in routing.routes.items():
        if route.prompt is None:
            continue
        if route.prompt not in lock:
            problems.append(f"prompt: step {step.value} uses {route.prompt}, not in the lock file")
        elif prompts.get(route.prompt).sha256 != lock[route.prompt]:
            problems.append(f"prompt: step {step.value} uses {route.prompt}, edited after release")
    for ref in targets:
        try:
            prices.price_for(ref)
        except ConfigError:
            problems.append(f"price: {ref} has none in config/prices.yaml")
    return problems


async def _ping_all(routing: Routing, targets: Targets, env: Mapping[str, str]) -> list[Row]:
    adapters = {
        name: build_adapter(routing.providers[name], env)
        for name in sorted({ref.provider for ref in targets})
    }
    rows: list[Row] = []
    try:
        for ref, (whats, kind) in targets.items():
            started = time.perf_counter()
            try:
                cost, detail = await _ping(adapters[ref.provider], ref, kind)
                ok = True
            except ProviderError as exc:
                cost, detail, ok = Decimal(0), str(exc)[:160], False
            took = round((time.perf_counter() - started) * 1000)
            rows.append(Row(", ".join(whats), ref, ok, took, detail, cost))
    finally:
        for adapter in adapters.values():
            await adapter.aclose()
    return rows


async def live_check(environ: Mapping[str, str] | None = None, out: TextIO = sys.stdout) -> int:
    env = dict(os.environ if environ is None else environ)
    budgets = Budgets.from_env(env)
    book = SpendBook.load()
    try:
        book.effective_budget(Budgets(run=Decimal("0.01"), total=budgets.total))
    except BudgetRefusedError as exc:
        out.write(f"refused: {exc}\n")
        return 3
    file = read_routing_file(DEFAULT_RESOURCES_DIR / "config" / "models.yaml")
    out.write("keys\n")
    for name, provider in sorted(file.providers.items()):
        if provider.api_key_env:
            key = mask(env.get(provider.api_key_env, ""))
            out.write(f"  {name:<10} {provider.api_key_env:<20} {key}\n")
    try:
        routing = resolve_routing(file, env, "live")
    except ConfigError as exc:
        out.write(f"{exc.message}\n")
        return 2
    targets = _targets(routing)
    problems = _problems(routing, targets)
    rows = await _ping_all(routing, targets, env)

    out.write("\nmodels\n")
    out.write(f"  {'result':<6} {'provider':<10} {'model':<24} {'latency':>8}  used by / detail\n")
    for r in rows:
        out.write(
            f"  {'ok' if r.ok else 'FAIL':<6} {r.ref.provider:<10} {r.ref.model:<24} "
            f"{r.latency_ms:>6} ms  {r.what}\n{'':<52}{r.detail}\n"
        )
    spent: dict[str, Decimal] = {}
    for r in rows:
        spent[r.ref.provider] = spent.get(r.ref.provider, Decimal(0)) + r.cost
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    book.record(
        run_id=f"live-check-{stamp}",
        suite="live-check",
        spent_by_provider=spent,
        budget=Decimal("0.01"),
        batch=budgets.batch,
    )
    priced = not any(p.startswith("price") for p in problems)
    locked = not any(p.startswith("prompt") for p in problems)
    out.write(f"\nprices: {'every model has one' if priced else 'MISSING'}\n")
    out.write(f"prompts: {'every step is released in the lock' if locked else 'PROBLEM'}\n")
    for problem in problems:
        out.write(f"  {problem}\n")
    total = usd(sum(spent.values(), Decimal(0)))
    out.write(f"cost: ${total:.6f}\n{book.summary(budgets.total)}\n")
    return 0 if all(r.ok for r in rows) and not problems else 1
