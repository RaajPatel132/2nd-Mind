# ADR-0024: Vector storage: exact scan per workspace now, HNSW on halfvec(1536) at scale

- **Status:** accepted (provisional on real embeddings, see Consequences)
- **Date:** 2026-09-26

## Context

The soft channel (S3.6) runs a dense top-k over every key of one workspace on every recall turn,
under RLS, next to the filtered tools; the rule is that no filter is the only path to a memory,
so its recall matters as much as its latency. Keys are `vector(1536)` (text-embedding-3-small)
with no vector index: the query is an exact scan of the workspace's rows, found through the
`workspace_id` btree. D6 asked whether to add an ANN index or shrink the vectors before there
is user data to re-embed.

`make bench-vectors` (`backend/tools/bench_vectors.py`) loads 1k, 10k and 50k-item workspaces
(4 keys per item) beside 20 other workspaces (284k keys) into pgvector 0.8 / Postgres 16, and
times the soft channel's query as a non-owner role under the same RLS policy (40 queries,
`hnsw.iterative_scan = relaxed_order`, `ef_search = 40`, 4 vCPU, default `shared_buffers`).
This run used **clustered random vectors** (no OpenAI key in the environment):

| variant | items | plan | p50 ms | p95 ms | recall@10 | build s | index MB |
|---|---:|---|---:|---:|---:|---:|---:|
| exact | 1,000 | exact | 20.86 | 23.92 | 1.000 | — | — |
| exact | 10,000 | exact | 264.54 | 305.71 | 1.000 | — | — |
| exact | 50,000 | exact | 771.38 | 1066.11 | 1.000 | — | — |
| hnsw vector(1536) | 1,000 | hnsw | 21.86 | 27.91 | 0.958 | 533.9 | 2,219 |
| hnsw vector(1536) | 10,000 | hnsw | 6.91 | 9.46 | 0.797 | 533.9 | 2,219 |
| hnsw vector(1536) | 50,000 | hnsw | 4.30 | 4.89 | 0.728 | 533.9 | 2,219 |
| hnsw halfvec(1536) | 1,000 | hnsw | 21.52 | 25.90 | 0.958 | 139.6 | 1,109 |
| hnsw halfvec(1536) | 10,000 | hnsw | 7.00 | 9.08 | 0.800 | 139.6 | 1,109 |
| hnsw halfvec(1536) | 50,000 | hnsw | 4.35 | 6.44 | 0.708 | 139.6 | 1,109 |
| hnsw halfvec(768) | 1,000 | hnsw | 19.63 | 27.99 | 0.512 | 92.6 | 555 |
| hnsw halfvec(768) | 10,000 | hnsw | 6.31 | 9.79 | 0.260 | 92.6 | 555 |
| hnsw halfvec(768) | 50,000 | hnsw | 4.66 | 6.77 | 0.208 | 92.6 | 555 |

## Decision

- **Keep `vector(1536)` and the exact scan per workspace.** No migration, no re-embedding,
  `EMBED_DIMENSIONS` stays 1536. At the sizes we will see for a while (hundreds to a few
  thousand memories per person) it is ~20 ms and never misses; an HNSW index on a small
  workspace is no faster (the iterative scan walks other workspaces' nodes) and loses recall.
- **When it's needed, add HNSW on `halfvec(1536)`,** as an expression index on the existing
  column (`(embedding::halfvec(1536)) halfvec_cosine_ops`), not a second column. Half the
  index of `vector(1536)` and a quarter of the build time, for the same latency and recall.
- **The trigger:** a workspace passing ~3,000 memories (~12k keys), or soft-channel p95 over
  100 ms in the Timing & cost spans. The switch also reshapes the dense query so the index can
  serve it (order by distance alone, no tie-break column; `relaxed_order`; `ef_search` raised
  until recall@10 is at least 0.95 on real embeddings).
- **Not 768 dimensions.** Truncation only works for vectors trained for it; random vectors
  can't show whether text-embedding-3's 768-d prefix keeps enough, so it stays unproven.

## Alternatives considered

- **HNSW on `vector(1536)` now.** Same speed as halfvec with twice the memory and a 9-minute
  build at this size, and recall 0.73 to 0.80 at `ef_search = 40` is not acceptable for the
  channel whose job is to never miss.
- **`halfvec(768)` now.** The smallest and cheapest, but it would change the column and every
  stored embedding on the strength of a number random vectors can't give.
- **IVFFlat.** Needs training data per list and re-building as workspaces grow; HNSW with
  iterative scans handles the per-workspace filter without tuning lists.

## Consequences

Nothing changes in the schema this sprint. The numbers are **provisional**: latency, build time
and size carry over to real embeddings, but recall@10 does not (random clusters in 1536
dimensions have far less neighbour structure than text embeddings, so HNSW and especially the
768-d prefix look worse here than they will be). Re-run `make bench-vectors` with
`OPENAI_API_KEY` set (about $0.09, capped at $0.50) before the switch, and revisit this ADR with
those numbers; if the 768-d prefix holds recall there, it becomes the switch's target instead.
