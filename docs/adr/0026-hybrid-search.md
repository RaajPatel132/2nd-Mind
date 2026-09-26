# ADR-0026: Hybrid search: ts_rank_cd, RRF fusion and a listwise model reranker

- **Status:** accepted
- **Date:** 2026-09-26

## Context

FR-6.2 combines lexical and dense retrieval with structured filters, fuses and reranks, and
the glass box shows the scores. Recall runs on Postgres (RDS in production, ADR-0012). What we
pick has to be available there, cheap to run per turn and explainable per candidate.

## Decision

- **Lexical:** Postgres full-text search, `ts_rank_cd` over a generated `tsvector` on each key,
  with an OR query of the question's words. It ships with Postgres and RDS; no extension.
- **Dense:** pgvector cosine distance on the key embeddings (storage choice: ADR-0024).
- **Inside a channel**, lexical and dense are fused by reciprocal rank fusion over a pool of
  keys, keys collapse to their item (best key kept, its kind recorded), and the channel
  returns items.
- **Across channels** (each tool's result plus the soft channel), RRF again with `RRF_K`.
  Agreement between channels raises an item; old rows (superseded, moved, dropped) are
  multiplied by `HISTORY_DEMOTION` except for `history` and `why` questions.
- **Rerank:** `rerank@1`, a listwise structured call on a small fast model, scores the top
  `RERANK_TOP_N` from each candidate's verbalised key, kind, state and dates, with a reason.
  Selection keeps what scores at least `RERANK_MIN_SCORE`; list-like shapes keep everything the
  filtered tools found and use rerank only for soft-only extras; counts cite what they counted.
  With rerank off or failing, the fused order is used with a floor.

## Alternatives considered

- **BM25 extensions (pg_search, VectorChord-bm25).** Better lexical scores, but not on RDS; a
  self-managed Postgres just for this is not worth it at our scale.
- **Weighted score sums instead of RRF.** Scores from different channels aren't comparable,
  and weights need tuning per workspace; RRF needs neither and is easy to show.
- **A cross-encoder reranker.** Needs a model server or a much bigger image and a GPU for
  latency; a listwise call on a small hosted model reads dates and states, which matter here.

## Consequences

Every candidate carries lexical, dense, RRF and rerank scores and the channels that found it,
so the Retrieval panel can explain any answer. Rerank adds one small model call per
sub-query with candidates; it is the first thing to switch off (`RERANK_ENABLED`) if cost or
latency needs it, and S5's ablations measure what that costs in quality.
