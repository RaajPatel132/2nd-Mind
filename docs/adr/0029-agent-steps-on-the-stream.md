# ADR-0029: Agent steps on the turn stream

- **Status:** accepted
- **Date:** 2026-09-25
- **Amends:** ADR-0015

## Context

The Trail (ADR-0028, `docs/design/system.md` §8) shows each agent step as it happens and the
same steps after a reload (FR-9.2). The stream carried reply tokens only, and events existed
only in `/events` after the turn. The UI can only animate honestly what the server reports.

## Decision

- **`AgentStep`** is a `StrEnum` in `core` holding the whole catalogue: `understand`, `extract`,
  `dates`, `entities`, `reconcile`, `enrich`, `guard`, `save`, `answer`, `undo`, `confirm`, and
  the reserved `plan`, `search`, `rank`, `triggers` (S3) and `fetch` (S4). It is in the
  OpenAPI schema, so the frontend's `Record<AgentStep, StepSpec>` fails to compile when a
  step has no entry.
- **A persisted `step` event (v1):** `step`, `status` (`done` | `held` | `refused` | `failed`),
  `started_at`, `latency_ms`. One per step that ran; steps that didn't run are not reported.
- **Written with the step's events.** Work runs inside `trail.run(step)`; events emitted
  meanwhile are buffered and written with the `step` event in one transaction
  (`TurnStore.append_many`), `model_call` ledger rows included. A step that raises is
  recorded as `failed` and nothing after it runs.
- **Two new frames:** `step.started {step, at}` when a step begins, and `turn.event {seq,
  event}` for every event as it is persisted (same `seq` and body as `/events`). Reply tokens
  go through the same queue, so frames arrive in the order things happened. Existing frames
  are unchanged; a client that ignores the new ones still works.
- **Order in a save:** understand, extract, entities, dates, reconcile, guard, save, enrich,
  answer. `guard` and `save` share one memory transaction: the writer measures the time spent
  on policy verdicts inside it, `guard` gets that latency, and `save` is reported (with the
  memory diff) only when something was actually saved, its start frame sent as the commit
  returns. A refused secret shows `guard` refused and no `save`.
- Undo and confirm turns report `undo` / `confirm`; housekeeping (system) turns report none.

## Alternatives considered

- **Derive steps in the browser from existing events:** no live timing, and "running" would
  be a guess, which the rulebook forbids.
- **Stream steps without persisting them:** a reload would look different from the live turn.
- **LangGraph's custom stream for tokens and steps:** delivery order against events written
  from inside nodes isn't guaranteed; one asyncio queue is.

## Consequences

- Every turn stores a few more small events; `/events` and the stream carry them.
- New backend work that the user should see needs an `AgentStep` value and a `steps.ts` entry.
- The guard/save split is measured, not two transactions; if they ever become separate
  writes, the split becomes exact without changing the frames.
