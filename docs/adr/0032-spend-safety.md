# ADR-0032: Spend safety: kill switch, caps, provider credit, dollar quotas, rate limits

- **Status:** accepted (the daily cap gains a guests' share, ADR-0039)
- **Date:** 2026-09-28
- **Amends:** ADR-0030 (the quota is counted in dollars)

## Context

Everything runs on prepaid credit ($10 Anthropic, $6 OpenAI) that pays for development, eval
runs and every visitor. NFR-3.1–3.4 and FR-12.1/12.2 ask for per-identity quotas, global spend
caps, a kill switch that needs no deploy, and rate limits. A per-person quota alone can't
protect the credit: enough people each within their quota still spend it all. A provider whose
balance runs out answers with an error, and without handling that the app would crash turns.

## Decision

**One gate before a turn** (`metering.SpendGate`), checked in order; the first that applies
blocks the turn:

| Block | Source | Scope |
|---|---|---|
| `kill_switch` | Redis flag `sm:kill_switch` (`make kill-switch on\|off`), else `KILL_SWITCH` | app |
| `daily_cap`, `monthly_cap` | Redis counters per UTC day and month vs `SPEND_CAP_DAILY_USD` ($0.50), `SPEND_CAP_MONTHLY_USD` ($5) | app |
| `provider_credit` | both providers' credit used up (below) | app |
| `quota` | the person's lifetime spend vs their tier's `QUOTA_USD_*` | person |

A blocked turn is still a turn: it is stored with a `blocked` event (reason, limit, value) and
a `blocked` Trail step, and its reply is a template. **No model call is made.** The composer
reads the same state from `GET /v1/me/usage` (`read_only`) and says why, and what still works:
browsing, Upcoming, undo and the glass box never call a model. NFR-6.2's provider outage uses
the same read-only template.

**Counters.** Every priced call adds its cost to the Redis counters as it returns (a hook on
the model router, so background calls count too). Global totals aren't workspace data, so they
live in Redis; the usage ledger stays the source of truth, and a worker job reconciles the
counters from it every 10 minutes through a `SECURITY DEFINER` function that returns totals
only (`global_spend`). At `SPEND_CAP_WARN_RATIO` (0.8) of a cap, a structured
`spend.cap_warning` log event is written once per period (alerts arrive in S8). If Redis can't
be read, the gate fails closed.

**Overshoot.** A turn already admitted finishes: the gate is checked once, before the turn
starts, so the kill switch or a cap never leaves a turn half-written. The overshoot is at most
the turns in flight at that moment (one per process at demo load). Everything else stops
within seconds: each process re-reads the flag at most every 2 s, the router refuses any call
that isn't part of an admitted turn while the switch is on or a cap is reached, and background
jobs that need a model are deferred (re-queued for later), not dropped.

**Provider credit.** `PROVIDER_CREDIT_USD_ANTHROPIC` and `PROVIDER_CREDIT_USD_OPENAI` are what
the app may spend on each provider, counted from `PROVIDER_CREDIT_SINCE`; set them below the
real balance. When one is used up, or the provider itself answers "out of credit"
(`ProviderErrorKind.CREDIT`), the router skips that provider for an hour and its steps go to
their fallback on the other provider. When both are out, turns are blocked (`provider_credit`).

**Quota in dollars** (amends ADR-0030). The ledger stores every call's cost, so the quota
counts dollars spent, lifetime, per tier: `QUOTA_USD_GUEST` $0.75, `QUOTA_USD_STANDARD` $2.50,
`QUOTA_USD_PREMIUM` $4.00. System usage (background indexing, housekeeping) goes on the ledger
marked `system` and isn't charged to the person. Weighted tokens stay on each call as
information. `users.tier` (default `standard`) is set by an admin CLI (`make set-tier`), and
every change is audited in `tier_changes` (who, when, from, to). The tier decides the quota
and the models a person can pick (ADR-0031).

**Rate limit.** `RATE_TURNS_PER_MINUTE` per identity, a Redis token bucket; over it, the API
answers `429` with `Retry-After`. A rate-limited request is refused at the door, not stored as
a turn: storing a turn per rejected request would make the limit a way to fill the database.

**Operating it** (`python -m secondmind.api.admin`, behind `make`):

- `make kill-switch on|off` flips the Redis flag; every api and worker process re-reads it at
  most every 2 s, so it needs no restart. `make spend-now` prints the counters. `KILL_SWITCH`
  only sets the value at start-up; the runtime flag wins once it exists.
- `make set-tier EMAIL=… TIER=guest|standard|premium` runs as the schema owner (the app's role
  can read `tier_changes` but not write it), and writes the audit row (who, when, from, to) in
  the same transaction. The person's quota and picker follow on their next turn.

**Background work.** A job that needs a model checks the gate first; if it is stopped, it is put
back on the queue for five minutes later (a new job, so a long stop never uses up a failing
job's retries). Embeddings the app pays for (indexing what was said, re-rendering keys) are on
the ledger as `system` usage against the turn they belong to, so the caps see every dollar and
the person's quota never does.

**Approximations, stated.** (1) A call that returns a reply the schema rejects is billed by the
provider but has no ledger row (there is no event to hang it on); the router counts it toward
the spend counters until the next reconcile replaces them with the ledger's figures. (2) The
counters are per UTC day and month; a process that dies between a call and its count leaves the
counters low until the next reconcile (every 10 minutes). (3) The caps can be passed by the
turns already admitted when they are reached; at demo load that is one turn per process. The
caps are set with room for that, and `PROVIDER_CREDIT_USD_*` below the real balance.

## Alternatives considered

- **Enforce caps in the ledger with SQL on every call:** exact, but a cross-workspace sum on
  every model call, under RLS, on the hot path.
- **Cut off turns in flight when a cap is reached:** a tighter cap, at the price of half-written
  turns, which NFR-6.1 rules out.
- **Token quotas (ADR-0030):** a budget people can't read, and not what the credit is in.

## Consequences

Redis is now on the turn path; readiness already requires it. The caps protect the credit only
as far as the price table is right: a price change needs `prices.yaml` updated the same day.
Revisit the defaults when real users arrive (S6) and when guests get their own caps (S4).
