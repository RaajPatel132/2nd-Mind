# ADR-0030: A model picker and a quota weighted by model price

- **Status:** accepted
- **Date:** 2026-09-25
- **Amends:** ADR-0013

## Context

Routing is per step and set by config (FR-14.2), so a person can't choose the model that
answers them. The quota (FR-12.1) counted raw tokens, so a Fable turn and a Haiku turn of the
same length cost the allowance the same, although one costs ten times the other. PRD §7.12
says the metered number must track real cost.

## Decision

- **A baseline model** (`baseline` in `config/prices.yaml`, Claude Sonnet 5). Quota is counted
  in baseline tokens. A model's **weight** is `(3 × input + output)` price over the same for
  the baseline, to four places: Opus 5 is 2.5, Fable 5.1 is 5, Haiku 4.5 is 0.5.
- **Every call is charged `round(total tokens × weight)`**, worked out by the router from the
  model that actually served it (a fallback is charged as the fallback). The charge is stored
  on the call's `usage`, the ledger row and the turn (`charged_tokens`), so a later price
  change never rewrites what was spent. Rows from before this are backfilled at weight 1.
- **A picker** (`picker` in `config/models.yaml`): the models a person can choose, grouped by
  provider, and a default. A turn may name one (`model` on `POST .../turns`); it then runs
  every chat step on it, keeping each step's fallback unless that is the same model. Embeddings
  keep their route. No `model` keeps the configured routing (API clients, system turns).
- **Without credentials** for a picked model's provider, `auto` and `fake` modes let the fake
  provider stand in for it (`fake:<model>`), **priced as that model**, so the quota behaves as
  it would for real. `live` shows the model as unavailable and refuses it.
- `/v1/meta` returns the picker with each model's weight, and the UI shows the weight next to
  each model.

## Alternatives considered

- **Charge cost in dollars:** exact, but people can't read a budget of $0.0031. Baseline tokens
  keep the number people already see and still track cost.
- **Weight each token class separately:** slightly more exact with heavy caching, but no longer
  one number the picker can show next to a model.
- **Charge fake stand-ins at the fake price:** honest about the server's own cost, but in local
  development every model would drain the quota the same, which hides the feature.

## Consequences

- One pick drives the whole turn: picking Fable makes extraction and enrichment Fable too. A
  per-step choice would need a different control.
- The 3:1 blend is a guess at the mix; a turn heavy on output is charged a little less than
  its true cost ratio. Revisit when S4 enforces the quota, with real turn mixes.
- Every picker model needs a price, checked at start-up, and a new model means a price and a
  line in `models.yaml`.
