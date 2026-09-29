# ADR-0024: Vector storage: exact scan per workspace now, HNSW on halfvec(1536) at scale

- **Status:** accepted (measured on real embeddings, 2026-09-29)
- **Date:** 2026-09-26
- **Revised:** 2026-09-29 (Sprint 3.9: the bench re-run on real `text-embedding-3-small` vectors)

## Context

The soft channel (S3.6) runs a dense top-k over every key of one workspace on every recall turn,
under RLS, next to the filtered tools; the rule is that no filter is the only path to a memory,
so its recall matters as much as its latency. Keys are `vector(1536)` (text-embedding-3-small)
with no vector index: the query is an exact scan of the workspace's rows, found through the
`workspace_id` btree. D6 asked whether to add an ANN index or shrink the vectors before there
is user data to re-embed.

`make bench-vectors` (`backend/tools/bench_vectors.py`) loads 1k, 10k and 50k-item workspaces
(4 keys per item) beside 20 other workspaces (284k keys in all) into pgvector 0.8 / Postgres
16, and times the soft channel's query as a non-owner role under the same RLS policy (40
queries, `hnsw.iterative_scan = relaxed_order`, `ef_search = 40`, 4 vCPU, default
`shared_buffers`). The first run had no OpenAI key and used clustered random vectors, which
have far less neighbour structure than text; its recall numbers (0.71 to 0.80 for HNSW, 0.21
to 0.51 at 768 dimensions) were marked provisional. **This run embeds the templated sentences
with the real model** (cost $0.06, run in batch B6 of Sprint 3.9):

| variant | items | plan | p50 ms | p95 ms | recall@10 | build s | index MB |
|---|---:|---|---:|---:|---:|---:|---:|
| exact | 1,000 | exact | 23.04 | 29.50 | 1.000 | — | — |
| exact | 10,000 | exact | 287.51 | 419.71 | 1.000 | — | — |
| exact | 50,000 | exact | 631.76 | 1527.88 | 1.000 | — | — |
| hnsw vector(1536) | 1,000 | hnsw | 190.09 | 506.81 | 0.983 | 287.0 | 1,199 |
| hnsw vector(1536) | 10,000 | hnsw | 7.48 | 11.58 | 1.000 | 287.0 | 1,199 |
| hnsw vector(1536) | 50,000 | hnsw | 7.50 | 12.56 | 0.975 | 287.0 | 1,199 |
| hnsw halfvec(1536) | 1,000 | hnsw | 98.58 | 157.16 | 0.983 | 57.7 | 762 |
| hnsw halfvec(1536) | 10,000 | hnsw | 7.92 | 14.26 | 0.960 | 57.7 | 762 |
| hnsw halfvec(1536) | 50,000 | hnsw | 4.33 | 7.21 | 0.953 | 57.7 | 762 |
| hnsw halfvec(768) | 1,000 | hnsw | 14.83 | 23.52 | 0.943 | 29.6 | 380 |
| hnsw halfvec(768) | 10,000 | hnsw | 2.36 | 3.55 | 0.935 | 29.6 | 380 |
| hnsw halfvec(768) | 50,000 | hnsw | 1.47 | 3.35 | 0.927 | 29.6 | 380 |

Three things the real vectors show that random ones could not:

- HNSW recall is much better than the provisional numbers: 0.95 to 1.00 on `halfvec(1536)`
  and `vector(1536)` at `ef_search = 40`, against 0.71 to 0.80 on random vectors.
- The 768-dimension prefix is usable but not equal: 0.93 to 0.94 on real text against 0.21 to
  0.51 on random vectors, yet still below the 0.95 the channel asks for ("never miss") at the
  same `ef_search`.
- A small workspace beside many others is the worst case for an index: at 1,000 items the
  iterative scan walks other workspaces' nodes and takes 99 to 190 ms, against 23 ms exact.

## Decision

- **Keep `vector(1536)` and the exact scan per workspace.** No migration, no re-embedding,
  `EMBED_DIMENSIONS` stays 1536. At the sizes we will see for a while (hundreds to a few
  thousand memories per person) it is ~23 ms and never misses; an HNSW index on a small
  workspace is slower (99 to 190 ms at 1,000 items, measured) and can miss (0.98).
- **When it's needed, add HNSW on `halfvec(1536)`,** as an expression index on the existing
  column (`(embedding::halfvec(1536)) halfvec_cosine_ops`), not a second column. Two thirds of
  the index of `vector(1536)` (762 MB against 1,199 MB) and a fifth of the build time (58 s
  against 287 s), for latency 4 to 8 ms and recall@10 of 0.95 to 0.98 (0.96 at 10,000 items,
  0.953 at 50,000, both at `ef_search = 40`).
- **The trigger:** a workspace passing ~3,000 memories (~12k keys), or soft-channel p95 over
  100 ms in the Timing & cost spans. Measured: the exact scan is 30 ms p95 at 1,000 items and
  420 ms at 10,000, so 100 ms falls near 3,000. The switch also reshapes the dense query so the
  index can serve it (order by distance alone, no tie-break column; `relaxed_order`;
  `ef_search` raised until recall@10 is at least 0.95 at the workspace size that triggered it).
- **Not 768 dimensions, not now.** On real text the prefix holds 0.93 to 0.94, the smallest
  and fastest index (380 MB, 30 s build) but 2 to 3 points under the bar at the same
  `ef_search`. It becomes the target only if an `ef_search` sweep at the trigger size lifts it
  to 0.95 without costing what halfvec(1536) saves, and that would be a migration with a
  re-embed.

## Alternatives considered

- **HNSW on `vector(1536)` now.** The best recall (0.975 to 1.000) but the largest index
  (1.2 GB for this table) and a 5-minute build, and at 1,000 items it is 8 times slower than
  the exact scan it would replace.
- **`halfvec(768)` now.** The smallest and cheapest, but it would change the column and every
  stored embedding for recall that measures under the bar (0.927 at 50,000 items).
- **IVFFlat.** Needs training data per list and re-building as workspaces grow; HNSW with
  iterative scans handles the per-workspace filter without tuning lists.

## Consequences

Nothing changes in the schema. The choice made on random vectors survives real ones, and the
open question (does HNSW recall hold on text?) is answered: yes on `halfvec(1536)`, marginally
no on the 768-d prefix. What to watch: the switch is a migration (an index on an expression,
built `CONCURRENTLY`) plus the query change above, and it should be rehearsed on a copy of the
biggest workspace first; recall at `ef_search = 40` dips to 0.953 at 50,000 items, so the
switch checks it rather than assuming it. The bench costs $0.06 to re-run and stays a Make
target (`make bench-vectors` with `OPENAI_API_KEY`; `LIVE_RUN_BUDGET_USD` caps it).
