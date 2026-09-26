# ADR-0025: Query planning: the model picks the shape, code does the rest

- **Status:** accepted
- **Date:** 2026-09-26

## Context

FR-6.1 asks recall to plan each question: which filters, which layers, what to search for. A
model is good at reading what a question is *about* ("the weekend before Goa" is an order
question anchored on an event) and bad at the arithmetic and bookkeeping behind it: it works
out dates wrongly, invents people and relations, guesses vocabulary that doesn't exist, and
picks tools inconsistently from run to run. Recall errors are silent: a wrong filter returns
nothing, and nothing looks like "you never saved that".

## Decision

- `plan@1` (structured output) returns sub-queries, each with one of 13 **shapes** and its
  arguments. Time expressions are copied **verbatim** from the message, never worked out;
  `validate_plan` rejects a plan whose expressions aren't in the message or recent turns, and
  the planner retries once with the errors. Still invalid, or the planner is down: one
  `semantic` sub-query over the whole message (the soft channel still answers). The event
  records `plan_source` (`model`, `retry`, `fallback`).
- **Code resolves** everything the model named: windows through S2's resolver
  (`resolve_window`, with anchors looked up first), entities with S2's deterministic matcher,
  relation paths followed through stored relations (symmetric and inverse relations known),
  and kinds, subtypes, categories and predicates normalised against the vocab. Anything
  unknown is **dropped** and shown in the glass box, never guessed.
- **Code picks the tools**: `SHAPE_TOOLS` maps each shape to its tools, pinned by a test.
  The soft channel runs for every shape.
- Situational questions: the planner may add sub-queries from core memory, each naming the
  core line it came from (dropped if that line isn't in core); code always adds what's booked
  in the window and the unconsumed resources of the chosen intentions.

## Alternatives considered

- **Tool calling (the model calls search tools in a loop).** More model calls per question,
  harder to bound latency and cost, and tool choice varies from run to run; the glass box
  would show a transcript instead of a plan.
- **No planner (hybrid search only).** Can't count, order, follow "Nisha's husband", or
  respect "last month" as a filter on when something happened.

## Consequences

Plans are small, cheap and inspectable, and recall degrades to meaning search instead of
failing. The shape list is a contract: a new kind of question means a new shape, its tools in
`SHAPE_TOOLS` and golden cases. Golden cases replay recorded plans, so a prompt change is
measured by `make eval-recall-live`, not by the replayed suite.
