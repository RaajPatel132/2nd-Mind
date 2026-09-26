# ADR-0027: Corrections: reclassify, correct or supersede

- **Status:** accepted
- **Date:** 2026-09-26

## Context

FR-10.2/10.3: people fix memory by chat ("No, Mindhunter is a series") and in place from the
glass box. "That's wrong" means different things: filed wrongly, never true, or no longer true.
Treating them alike breaks history: if a mistake becomes history, "where did Nisha live
before?" repeats the mistake; if a change in the world overwrites the old row, history is lost.

## Decision

A `correct@1` step (structured) picks the target (the previous turn's writes first, else a
search result, the assumption stated) and one of:

- **reclassify:** kind, subtype, category, tags, format, layer, state, or a wrongly resolved
  date is an `update` op with before and after in the diff. A new date comes from the
  person's words through the resolver, never from the model.
- **wrong value:** a new row `corrects` the old one; the old row is **archived as a mistake**
  (out of recall and out of `history`; its verbalised key says "recorded by mistake, corrected
  on …"). A wrong relation is replaced (`unrelate`, then `relate`).
- **supersede** stays what S2 made it: a change in the world is a save that supersedes.
- **forget** is a soft delete, held by `P-BULK-1`; a **bulk** re-file over the threshold is held.

A "yes" to an offer in the previous reply (the count cross-check's "File them as runs?") is
routed to corrections by rule and applies the fix it named. A glass-box edit is `PATCH
/v1/items/{id}`, run as its own turn (kind `edit`, origin `ui_edit`, a user origin for the
policy). Every path goes through `MemoryWriter`, so the diff shows it and undo reverses it, and
keys are re-rendered at once.

## Alternatives considered

- **Always supersede.** Keeps a mistake as history, and "before" questions repeat it.
- **Hard delete the wrong row.** Loses the audit trail and makes undo impossible.

## Consequences

Three write paths to keep apart in code and tests, and one more link type (`corrects`). The
"from now on" part of a bulk correction is acknowledged but not learned until rules arrive
(S6).
