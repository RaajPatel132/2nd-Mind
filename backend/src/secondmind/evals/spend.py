"""What live runs have spent (R.1): one running total across every live command, kept in a small
gitignored file (``evals/runs/.spend``), so a sprint can't spend past its budget by accident.

Every live run adds its cost here when it ends. A run refuses to start when the total plus its
own budget would pass ``LIVE_TOTAL_BUDGET_USD``. Runs can be labelled with a batch of the
spending plan (``LIVE_BATCH``, allowances in ``evals/batches.yaml``): a batch can't be given more
than its allowance, and a batch that ends up spending over 125% of it halts every later run
until the halt is cleared (the stop rule). Amounts are the ledger cost of each call, from the
price table: the provider's own balance is the final word.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from secondmind.config import DEFAULT_RESOURCES_DIR
from secondmind.core import usd

RUNS_DIR = DEFAULT_RESOURCES_DIR / "evals" / "runs"
SPEND_FILE = RUNS_DIR / ".spend"
BATCHES_FILE = DEFAULT_RESOURCES_DIR / "evals" / "batches.yaml"

DEFAULT_RUN_BUDGET = Decimal("0.50")
# Sprint 4 counts its own live spend (S4.1): $1.50, not the $4 that S3.9 had.
DEFAULT_TOTAL_BUDGET = Decimal("1.50")
# A batch that spends more than this share of its allowance stops the plan.
STOP_RATIO = Decimal("1.25")


class BudgetRefusedError(Exception):
    """A live run that must not start (or go on): the message says why and what's left."""


def _money(value: object) -> Decimal:
    return usd(Decimal(str(value)))


@dataclass(frozen=True, slots=True)
class Budgets:
    """The limits a live run works within, from the environment (tooling only)."""

    run: Decimal = DEFAULT_RUN_BUDGET
    total: Decimal = DEFAULT_TOTAL_BUDGET
    batch: str | None = None
    allow_expensive: bool = False
    # A live run refuses a tree with uncommitted changes under backend/ unless this is set, and
    # the run file records that it was (ledger 45).
    allow_dirty: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Budgets":
        env = os.environ if environ is None else environ

        def amount(name: str, default: Decimal) -> Decimal:
            raw = env.get(name, "").strip()
            if not raw:
                return default
            value = Decimal(raw)
            if value < 0:
                raise BudgetRefusedError(f"{name} must not be negative")
            return value

        return cls(
            run=amount("LIVE_RUN_BUDGET_USD", DEFAULT_RUN_BUDGET),
            total=amount("LIVE_TOTAL_BUDGET_USD", DEFAULT_TOTAL_BUDGET),
            batch=env.get("LIVE_BATCH", "").strip() or None,
            allow_expensive=env.get("ALLOW_EXPENSIVE", "").strip() in {"1", "true", "yes"},
            allow_dirty=env.get("ALLOW_DIRTY", "").strip() in {"1", "true", "yes"},
        )


def read_allowances(path: Path = BATCHES_FILE) -> dict[str, Decimal]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(name): _money(spec["allowance_usd"]) for name, spec in raw["batches"].items()}


@dataclass(slots=True)
class SpendBook:
    """The running total, per provider and per batch, and the runs that made it."""

    path: Path = SPEND_FILE
    total: Decimal = Decimal(0)
    by_provider: dict[str, Decimal] = field(default_factory=dict)
    by_batch: dict[str, Decimal] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    halted: dict[str, str] | None = None

    @classmethod
    def load(cls, path: Path = SPEND_FILE) -> "SpendBook":
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            total=_money(raw.get("total_usd", 0)),
            by_provider={k: _money(v) for k, v in raw.get("by_provider", {}).items()},
            by_batch={k: _money(v) for k, v in raw.get("by_batch", {}).items()},
            runs=list(raw.get("runs", [])),
            halted=raw.get("halted"),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": 1,
            "total_usd": str(self.total),
            "by_provider": {k: str(v) for k, v in sorted(self.by_provider.items())},
            "by_batch": {k: str(v) for k, v in sorted(self.by_batch.items())},
            "halted": self.halted,
            "runs": self.runs,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def effective_budget(
        self, budgets: Budgets, allowances: Mapping[str, Decimal] | None = None
    ) -> Decimal:
        """What the next run may spend: its own budget, within the sprint total and its batch's
        allowance. Raises :class:`BudgetRefusedError` when it must not start at all."""
        if self.halted:
            raise BudgetRefusedError(
                f"live runs are halted: {self.halted.get('reason')} (since "
                f"{self.halted.get('at')}). Explain it in the sprint report, then clear it with "
                "`python -m secondmind.evals spend --clear-halt`."
            )
        left = budgets.total - self.total
        if self.total + budgets.run > budgets.total:
            raise BudgetRefusedError(
                f"this run's budget ${budgets.run:.2f} would pass the sprint budget: "
                f"${self.total:.4f} spent of ${budgets.total:.2f}, ${max(left, Decimal(0)):.4f} "
                "left"
            )
        budget = budgets.run
        if budgets.batch is not None:
            allowance = (allowances or read_allowances()).get(budgets.batch)
            if allowance is None:
                raise BudgetRefusedError(f"unknown batch {budgets.batch!r} (evals/batches.yaml)")
            batch_left = allowance - self.by_batch.get(budgets.batch, Decimal(0))
            if batch_left <= 0:
                raise BudgetRefusedError(
                    f"batch {budgets.batch} has used its ${allowance:.2f} allowance"
                )
            budget = min(budget, batch_left)
        return budget

    def record(
        self,
        *,
        run_id: str,
        suite: str,
        spent_by_provider: Mapping[str, Decimal],
        budget: Decimal,
        batch: str | None,
        allowances: Mapping[str, Decimal] | None = None,
        note: str | None = None,
    ) -> Decimal:
        """Add a finished run's spend; returns what it spent. Applies the stop rule."""
        spent = usd(sum(spent_by_provider.values(), Decimal(0)))
        self.total = usd(self.total + spent)
        for provider, amount in spent_by_provider.items():
            self.by_provider[provider] = usd(self.by_provider.get(provider, Decimal(0)) + amount)
        if batch is not None:
            self.by_batch[batch] = usd(self.by_batch.get(batch, Decimal(0)) + spent)
        self.runs.append(
            {
                "run_id": run_id,
                "suite": suite,
                "batch": batch,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "spent_usd": str(spent),
                "budget_usd": str(usd(budget)),
                "by_provider": {k: str(usd(v)) for k, v in sorted(spent_by_provider.items())},
                **({"note": note} if note else {}),
            }
        )
        if batch is not None:
            allowance = (allowances or read_allowances()).get(batch)
            if allowance is not None and self.by_batch[batch] > allowance * STOP_RATIO:
                self.halted = {
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "reason": (
                        f"batch {batch} spent ${self.by_batch[batch]:.4f}, more than 125% of its "
                        f"${allowance:.2f} allowance"
                    ),
                }
        self.save()
        return spent

    def clear_halt(self) -> None:
        self.halted = None
        self.save()

    def summary(self, total_budget: Decimal) -> str:
        left = max(total_budget - self.total, Decimal(0))
        parts = ", ".join(f"{p} ${v:.4f}" for p, v in sorted(self.by_provider.items()))
        line = f"live spend ${self.total:.4f} of ${total_budget:.2f} (${left:.4f} left)"
        if parts:
            line += f": {parts}"
        if self.by_batch:
            batches = ", ".join(f"{b} ${v:.4f}" for b, v in sorted(self.by_batch.items()))
            line += f"; by batch: {batches}"
        if self.halted:
            line += f"\nHALTED: {self.halted.get('reason')}"
        return line


@dataclass(slots=True)
class RunMeter:
    """What one run has spent so far, against its budget."""

    budget: Decimal
    spent_by_provider: dict[str, Decimal] = field(default_factory=dict)

    @property
    def spent(self) -> Decimal:
        return usd(sum(self.spent_by_provider.values(), Decimal(0)))

    def add(self, provider: str, amount: Decimal) -> None:
        if amount:
            self.spent_by_provider[provider] = usd(
                self.spent_by_provider.get(provider, Decimal(0)) + amount
            )

    def can_afford(self, next_estimate: Decimal) -> bool:
        return self.spent + next_estimate <= self.budget
