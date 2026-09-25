# ADR-0023: Extract and enrich as two model calls

- **Status:** accepted
- **Date:** 2026-09-25

## Context

A save needs structure (kind, state, entities, time expressions, predicate/value, modality,
sensitivity, layer) and retrieval aids (alternate phrasings and situational cues, ADR-0020).
One call could produce both. But the structured part must be right before anything is
written, while the retrieval aids only matter later and S5 needs to measure whether they help
at all.

## Decision

- **`extract@1`** (one call per message) returns the proposed memories and their structure.
  Its output is validated (JSON schema plus meaning-level checks such as unknown entity refs
  and unfilled placeholders). If it is invalid, the call is retried once with the errors; if
  it is still invalid, the turn fails cleanly with nothing written.
- **`enrich@1`** runs **after commit** on the items actually written. It writes `alt` keys
  and up to `CUE_KEYS_MAX` `cue` keys. It can be switched off (`ENRICH_ENABLED`) for the S5
  ablation, and if it fails the save still stands.
- Both calls share the stable core-memory prefix, which the provider caches (S2.9).

**Cost** was estimated from the 26 golden cases replayed on the fake provider (about 4
characters per token), priced at `claude-opus-5` rates: input $5, cached $0.50 and output $25
per million tokens.

| Step | Input tokens | Output tokens | Cost per save |
|---|---|---|---|
| extract | ~1,450 (64 cached) | ~200 | ~$0.012 |
| enrich | ~230 (64 cached) | ~15 recorded, ~150 expected live | ~$0.0015–0.005 |

So enrich adds about 12–40% to the model cost of a save, plus one embedding call per new key.
`make eval-ingest-live` replaces these estimates with measured numbers in the sprint report.

## Alternatives considered

- **One combined call.** Cheaper by one round trip, but cues would be written for items that
  are then held, blocked or reconciled away. A failure in the cue part would fail the save,
  and the ablation couldn't switch enrichment off without changing the extraction prompt.
- **Enrich in a background job.** Keeps the reply faster, but the glass box of the turn
  couldn't show the keys it made. This can be revisited if enrich latency matters.

## Consequences

A save costs two calls and slightly more latency (enrich runs after the reply's
acknowledgement is built, but inside the turn). Each prompt stays focused and testable on its
own, and S5 can measure recall with and without enrichment by flipping one variable.
