# Choosing models by measurement, on a $4 budget

Every step of a turn ran on one expensive model, with another as its fallback. That was a guess.
This is how the routing was replaced by one chosen from measured cases, what it cost to find out,
and what the numbers do and do not show. The decision itself is
[ADR-0031](../adr/0031-economy-routing-from-measurement.md); every figure below is worked out from
the stamped run files in [`backend/evals/runs/`](../../backend/evals/runs/), and the run ids are
given so each can be checked.

## The problem

A turn makes three to five model calls in a row (intent, plan, rerank, answer; extract and enrich
when saving). With every call on the top model, a save cost about $0.017 and a recall about $0.07 on
paper, and an outage of the first provider moved everything to a fallback ten times dearer again.
The whole sprint of live runs had $4 to spend, so each run had to be small, repeatable and
worth its cost.

## The method

- **Candidates, not opinions.** A: every chat step on GPT-6 Luna. B: Luna for the short structured
  steps, Claude Haiku 4.5 for `extract` and `answer` (the steps that write memory and that the
  person reads). C: B with the rerank moved to GPT-5.4 mini and reasoning off on `intent`.
  Sonnet 5 ran once, on the hardest cases only, as a reference.
- **Small sets first.** A 15-case probe (`@probe`) and a 10-case reference (`@reference`) screened
  ideas before a full suite (50 recall, 28 ingest cases) was paid for.
- **Hard limits in code.** A per-run budget, a per-batch allowance with a stop rule (a batch that
  runs 25% over halts every later run), and a refusal to start from uncommitted code, so a run id
  always names code that exists. Re-reading a case from the response cache costs nothing.
- **Every run is a file.** Stamped with the commit, the prompts, the model per step, the prices and
  the result of each case.

## What the runs showed

Recall, full suite (hit means the answer's source was found and cited):

| Routing | Run | Passed | Priced cost per case |
|---|---|---|---|
| A, all Luna | `20260928T181102Z-0902bba` | 37 of 50 | $0.0010 |
| B, Luna + Haiku | `20260928T173941Z-0902bba` | 40 of 50 | $0.0022 |
| B after the prompt fixes below | `20260929T115409Z-38bf988`, `20260929T122745Z-f3cf41f` | 46 of 51, 46 of 51 | $0.0019, $0.0021 |
| C, the routing that shipped | `20260929T160542Z-1b15a10` | 45 of 51 | $0.0058 |

The jump from 40 to 46 did not come from a better model. The first full runs mostly measured gaps
in our own prompts and code: five new prompt versions (`extract@4` to `@7`, `plan@3`, `rerank@3`,
`intent@3`) and four pipeline fixes came out of reading the failures. Ingestion went from 11 of 28
cases to 27 of 30 across the final routing's two runs (29 of 30 once three golden cases that were
themselves wrong were corrected and re-scored from the cache, runs `…T155932Z-1b15a10`,
`…T160404Z-1b15a10`, `…T161440Z-1b15a10`). Intent routing is 25 of 25 for $0.002 in all
(`20260929T132330Z-3ea2081`).

### The reference model

Sonnet 5 ran on the ten hardest recall cases with the same prompts, and stopped at its budget
after nine. On those nine:

| Routing | Passed | Priced cost per case |
|---|---|---|
| Sonnet 5 | 5 of 9 | $0.0291 |
| B (Luna + Haiku) | 7 of 9 | $0.0021, about 14 times less |
| C (shipped) | 7 of 9 | $0.0060, about 5 times less |

It did no better on the cases B missed, which is how we knew the misses were ours. The "15 times
cheaper" often quoted for this comparison is candidate B. The routing that shipped costs more than
B, and the next section says why.

### Why the shipped routing costs more than B

The rerank was the slowest step. On the 15-case probe its fresh calls took a median of 4.7 s on
Luna and 3.1 s on GPT-5.4 mini, and both scored 15 of 15
(`20260929T125953Z-ba7913b`, `20260929T130507Z-ba7913b`). Haiku as the reranker also scored 15 of
15 but cost more ($0.087 against $0.059 for the probe). So the rerank moved to mini, which is the
cheaper of the two fast options and not the cheapest overall: in the full run it is 72% of C's
priced cost, against 23% of B's. Roughly $0.0035 more per recall buys about 1.6 s off the slowest
step. That is a trade, not a saving, and it is recorded as one.

Not everything was kept. Reasoning off on `plan` dropped a 2-hop case (14 of 15) and was reverted;
reasoning off on `intent` lost nothing (25 of 25) and saves 1.5 s a turn, so it stayed.

### Fallbacks

Each fallback crosses providers at the same price level (Luna to Haiku, Haiku to mini, mini to
Haiku). With each provider switched off in turn on ten cases: OpenAI off, 10 of 10 on Haiku at
twice the cost; Anthropic off, 9 of 10 on mini at no extra cost
(`20260929T131759Z-c333553`, `20260929T132050Z-c333553`).

## What these numbers do not show

- **The sets are small and synthetic.** One case is 3 to 6 points of a suite, so "within 3 points"
  meant "within one case". The final routing's three full recall runs scored 46, 46 and 45 with a
  different few failing each time: that is the noise floor, not progress or regression.
- **Latency is still the open problem.** The 9 cases of the shipped routing's full run that made
  fresh calls took a median of 10.8 s from question to the end of the answer (p95 17.2 s), with
  the first token near 9.9 s, against targets of 3 s and 6 s. The steps run one after another, and
  the rerank (median 2.9 s) and the plan (median 2.8 s, with outliers past 8 s that set the p95)
  are the two largest. Prompt caching does not help here: Haiku 4.5 caches only prefixes of 4,096
  tokens and the core-memory prefix is under 1,500. [`docs/performance.md`](../performance.md)
  breaks the time down by step.
- **Situational questions are the weak shape:** hit@5 was 0 of 2 in the shipped routing's run,
  against a target of 0.75 for every shape. It is on the list to fix, not hidden in the average.
- **A re-read from the cache is not a timing.** Most of the full run's cases were replayed from
  the response cache to keep the budget, so only the fresh calls are timed in the profile, and a
  probe whose calls were all cached (the rerank swap above) says nothing about end-to-end time.
- **Quality here is retrieval and extraction,** not how helpful the answers read. A judged
  faithfulness measure is later work.

## Reproduce

```bash
make eval-report A=20260929T160542Z-1b15a10                          # the shipped routing's full recall run
make eval-report A=20260928T173941Z-0902bba B=20260929T160542Z-1b15a10   # two side by side
make eval-latency A=20260929T160542Z-1b15a10                         # where the time goes, by step
make eval-recall                                                     # the same cases on replayed outputs, no keys
make eval-recall-live ROUTING=economy-c CASES=@probe                 # a small live run (cents, budgeted)
```
