# ADR-0020: Retrieval keys, verbalised keys and the soft channel

- **Status:** accepted
- **Date:** 2026-09-25

## Context

Recall (S3) will filter by kind, state, entity and time, then rank by text. Filters are
brittle: a wrong kind, a missed entity or an off-by-one time window loses the memory. A single
embedding of the stored text is brittle too. "What did I watch in September?" doesn't look
like "Watched Severance".

## Decision

- Items have **no search columns**. All search goes through `memory_keys`: one row per key
  with `key_kind` (`text`, `verbal`, `alt`, `cue`, `question`, `change`), its text, a
  `content_hash`, an embedding (dimension from `EMBED_DIMENSIONS`, checked at start-up) and a
  generated `tsvector` with a GIN index.
- **`text`** is the stored statement. **`verbal`** is one sentence rendered by a pure function
  in `memory/render.py` from the resolved fields: first person, absolute calendar words only
  (never "yesterday"), names with labels, kind and state in plain words, and units written
  out. For example: "Logged run: I ran 5 km in 31 minutes on Sunday 27 September 2026, in the
  morning, at Cubbon Park. Weekend, September 2026, exercise." **`change`** is written on
  supersede and move. **`alt`** and **`cue`** keys come from the separate `enrich` step
  (ADR-0023).
- Keys are **derived data**. They are rebuilt for touched items after every commit and every
  undo, are never write-log ops, and never appear in the diff. Renaming or relabelling an
  entity re-renders its items' keys in a background job, as a system turn.
- Embeddings are **cached by `content_hash`** within the workspace (keyed by the embedding
  model), so an unchanged sentence costs nothing; cache hits show on the `model_call` event.
- The **soft channel** (S3): when filters return too little, search the verbal keys without
  filters. `VERBAL_KEYS_ENABLED` and `SOFT_CHANNEL_ENABLED` exist for the S5 ablation.
- The vector index choice waits for S3 (ADR-0024). An exact scan is fine until then.

## Alternatives considered

- **Embed the item text only.** Cheapest, but misses time-phrased and situational queries.
- **Let the model write the verbal sentence.** Costs a call and can drift from the stored
  fields; the renderer says exactly what the filters would match on.

## Consequences

Each memory costs a few more rows and embeddings, softened by the hash cache. The renderer is
product copy with golden tests per kind and state change. Changing it re-renders keys, but
unchanged sentences keep their embeddings. Recall can be tuned (S5) by switching key kinds
off without a migration.
