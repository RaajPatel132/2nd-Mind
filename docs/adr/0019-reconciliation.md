# ADR-0019: Reconciliation instead of near-duplicate merge

- **Status:** accepted
- **Date:** 2026-09-25

## Context

A new memory often relates to one already stored: a new value for the same thing ("I moved to
Pune"), completion of a wish ("watched Severance"), a moved appointment, or a repeat of
something saved before. A "near-duplicate? merge" step gets all of these wrong. It merges two
different runs into one, overwrites the old city, and leaves the wish list stale.

## Decision

Every proposed memory is **reconciled** against candidates before it is written. The
candidates are items sharing an entity + predicate, plus items whose keys are similar above
`RECONCILE_SIMILARITY_THRESHOLD`. The decision is one of `new`, `add_detail`, `supersede`,
`fulfil`, `link` or `no_op`, and it is recorded in the Decision panel and the diff with the
candidate and the score.

Deterministic rules come first, in code:

- Same subject + predicate with a different value → **supersede**. The old row keeps its
  history: `valid_to` is set to the new `valid_from` (or the mention time), its state becomes
  `superseded`, and a `supersedes` link is written. A plan with a new time is `moved`.
- **Episodes never merge.** The only `no_op` for an episode is the same occurred time, the
  same entities and the same subtype.
- An episode about something on a wish list → **fulfil** the intention and link the episode.
- The same intention saved again → `no_op`.

Only the cases the rules don't settle go to the `reconcile` step (`reconcile@1` on a smaller
model). The model never decides supersession when subject and predicate match.

## Alternatives considered

- **Similarity merge.** Loses episodes and history; its errors are invisible and can't be undone.
- **Let the extract call decide.** One call doing everything is cheaper, but the decision
  would be unauditable and the model would be doing deterministic work.

## Consequences

"Where did I live before?" and "what have I watched from Nisha's list?" have direct answers,
and every reconciliation is undoable because supersede and fulfil are write-log ops. The
golden cases pin each rule (moved city, dentist moved, same show saved twice, watched twice,
two similar shows). The threshold needs tuning once there is live recall data (S5).
