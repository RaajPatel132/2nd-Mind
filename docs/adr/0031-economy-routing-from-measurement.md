# ADR-0031: Economy routing from measurement

- **Status:** accepted
- **Date:** 2026-09-29
- **Amends:** ADR-0013 (the default routing and fallbacks), ADR-0030 (the picker is tiered)

## Context

Every step ran on Opus 5.5 with GPT-6 Sol as the fallback: about $0.017 per save and $0.07 per
recall on paper, ten times dearer again after an Anthropic outage. The budget for the whole
sprint's live runs is $4, so the routing had to come from measured cases, on the cheapest models
that do the job. Every number below is in a stamped run file under `backend/evals/runs/`
(R.2); the ids are in the Sprint 3.9 report.

## Decision

**Candidates.** A: every chat step on GPT-6 Luna. B: Luna for the structured short-output steps,
Haiku 4.5 for `extract` and `answer`. Sonnet 5 ran once as a reference on the hardest 8 ingest
and 10 recall cases (`ALLOW_EXPENSIVE=1`); nothing above it ran at all.

| Run | Ingest | Recall | Cost |
|---|---|---|---|
| A, all Luna (full suites) | 11/28 | 37/50 | recall $0.046 |
| B, Luna + Haiku (full suites, same prompts) | 11/28 | 40/50 | recall $0.107 |
| B on the reference subset | 7/8 | 8/10 | $0.055 |
| Sonnet 5 on the same subset | 5/8 | 5/9 (stopped at budget) | $0.52 (about 15× per case) |

The first full runs mostly measured prompt gaps, not models: five new prompt versions
(`extract@4` to `@7`, `plan@3`, `rerank@3`, `intent@3`) and four pipeline fixes came out of
triaging them. Sonnet 5 did no better on the cases B missed, so the gaps were ours.

**Per step**, with the tolerance from the sprint (stay unless more than 3 points lost against
the reference, or any safety case fails; a step that fails moves up one rung):

| Step | Model | Why |
|---|---|---|
| intent | GPT-6 Luna, reasoning off | 25/25; off saves 1.5 s of every turn |
| resolve, enrich, reconcile, correct | GPT-6 Luna | short structured output; no case missed on them |
| plan | GPT-6 Luna, reasoning low | off flaked the 2-hop cases (14/15); kept |
| rerank | GPT-5.4 mini (was Luna) | Luna took 4 to 6 s; mini and Haiku both 15/15 in 3.2 s, mini is cheaper ($0.059 against $0.087 on the probe) |
| extract | Claude Haiku 4.5 | writes memory; Luna's extractions missed more kinds and states |
| answer | Claude Haiku 4.5 | what the person reads |

The tolerance is coarse on 18 to 30 cases per suite: one case is 3 to 6 points, so "within 3
points" means "no more than one case". The runs of the chosen routing rotate their few misses
(recall 46/51, 46/51, 45/51 with different failing cases), which is the noise floor.

**Fallbacks cross providers at the same price level:** Luna to Haiku, Haiku to GPT-5.4 mini,
mini to Haiku. Checked live on ten cases with each provider switched off in turn: OpenAI off,
10/10 on Haiku at twice the cost; Anthropic off, 9/10 on mini at no extra cost. Nothing falls
back to a model dearer than the next rung.

**The picker is tiered** (`picker.tiers` in `models.yaml`). Auto (no model sent) is the default
and is the routing above. Guests see no picker. A signed-in person may pick Luna, GPT-5.4 mini
or Haiku 4.5; an upgraded one also Sonnet 5. Fable, the Opus models and GPT-6 Sol leave the
picker but stay priced, so old turns cost correctly. Each choice shows its price against Auto's
for a typical turn (`picker.typical_turn` is the token profile). A pick the plan doesn't offer
is a 422.

**Output caps trimmed** to what the cases use (`extract` 8192 to 4096; the others by their
longest golden reply plus margin).

## Measured against the targets

| Target (PRD §9.4 and §5) | Measured | Verdict |
|---|---|---|
| Cost per save under $0.01 | about $0.011 (30 cases, $0.32) | on the line |
| Cost per recall under $0.01 | $0.0058 (51 cases) | met |
| First token p50 / p95 3 s / 6 s (recall) | 9.9 s / 15.5 s | not met |
| Total p50 (recall) | 10.8 s (p95 17.2 s) | not met |
| Intent p50 | 1.9 s | fine |

Latency is the finding: rerank (3 s) and the reasoning steps dominate, and the answer model is
the smaller share. Prompt caching does not help this routing: OpenAI caches automatically from
1,024 tokens, but Haiku 4.5 caches only prefixes of 4,096 tokens or more and the core-memory
prefix is at most about 1,500. The proposals, taken to Sprint 5 (ledger row 39): plan and
retrieve speculatively in parallel, and skip the rerank when nothing needs judging (one
candidate, or an exact key hit).

## Consequences

- `models.yaml` ships this routing; `make live-check` prices and pings every routed and picker
  model. A step moves up a rung only with a measurement in a run file.
- Ingest misses that remain are model errors and are counted against Haiku 4.5 (the gift date
  on the wrong clock in 02; see the report); recall misses are Luna plan variance on
  situational and 2-hop questions.
- The response cache in the live harness makes a re-run of unchanged cases free, but it is
  harness-only: the app's own provider path never uses it (a test pins that).
