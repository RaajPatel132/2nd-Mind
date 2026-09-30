# ADR-0038: The sample persona: template, copy and rebase

- **Status:** accepted (S4.10, S4.11)
- **Date:** 2026-10-01

## Context

FR-13.1 to FR-13.6. A visitor should open a believable memory in one click: no sign-up, nothing to
type first, and every save or recall they try should show the glass box. It has to be theirs alone
(changes never reach anyone else), it has to cost nothing to open (the copy must not call a model),
and it has to stay true on any date: "what's upcoming" must still be upcoming in a year.

## Decision

**A seed, loaded once into a template.** Aditi Rao (synthetic, in `backend/seeds/persona/aditi.yaml`)
is a workspace spec: entities, items, links, relations, past chats, and a few `series` (runs, gym
visits, episodes, dinners) that expand a template into many items. It is loaded through
`MemoryWriter` as one system turn, with keys rendered and embedded exactly as ingestion does. Saved
links get their `link_sources` row and their page's passages as chunk keys; an image and a PDF are
stored already extracted, `content_derived`, with no original file until S9. The loader moved from
`evals` into a `persona` module so the recall fixture and the persona load the same way.

**The template is a workspace of its own kind** (`template`), owned by a system user with no email.
Nobody signs in as that user, `resolve_scope` refuses the kind even for its owner, and it is left
out of every list and of the background jobs that sweep workspaces (so a job can't "tidy" it in real
time and make every copy differ). A deploy reloads it when the seed file's hash changes; copies
already made stay as they are. In production its embeddings are real and go on the ledger as the
app's own usage (about $0.01).

**A copy is a `SECURITY DEFINER` function, not application code.** The app role sees one workspace
at a time under RLS, so copying rows out of another workspace needs a door. `copy_persona_template`
is that door, and it is narrow: it accepts only a workspace whose kind is `template` as its source
(and only a new, empty `persona_copy` of the caller as its target), so it can't be used to duplicate
an ordinary workspace (tested). It copies every workspace-owned table that carries the persona
(`COPIED`), skips six on purpose (`SKIPPED`: events, the ledger, the write log, item versions, held
writes, access bookkeeping), and a catalog test fails when a table is in neither list. Ids are new;
turn ids are time-ordered UUIDs at the moved time, because history is paged by id.

**Every clock moves by whole days.** From the seed's anchor to today, by the calendar in the
persona's zone (Asia/Kolkata), never backwards. A routine moves by whole weeks, so it keeps its
weekday. Past turns move too. The seed's text carries no calendar date, no weekday (except in
something that recurs) and no relative day; a lint test enforces it, and the goldens run on copies
moved by 0, 60 and 400 days.

**The keys that say a date are re-rendered in code and keep the template's vector** (decision 10 of
the sprint). A key whose text reads "Tue 6 Oct" is rewritten with the new date and keeps the vector
computed when it said the old one. The alternative, re-embedding those keys as system usage, is
about $0.0001 a copy but calls a provider for every copy, and opening a copy would then fail with the
kill switch on. The trade-off is that the vector says "Sat 10 Oct" while the text says "Tue 1 Dec",
which changes similarity very little.

**One copy per person, and reset replaces it.** `POST /v1/persona` returns the caller's copy and
makes one the first time; `POST /v1/persona/reset` replaces it. A workspace records its seed id,
version and how many days it was moved.

## Alternatives considered

- **Generate the persona per visitor with a model:** slow, costly, and different every time, so no
  golden could hold it.
- **Copy in Python through the repositories:** it would need a way to read another workspace's rows
  from the app role, which is the thing RLS exists to prevent. A narrow definer function keeps that
  exception in one reviewed place.
- **Store the seed with literal dates and fix them on display:** every screen, query and key would
  have to know the offset. Moving the stored times is one place and makes every query true.

## Consequences

The template is a row like any other, so it is backed up and restored with the rest. A change to the
seed is a deploy; old copies keep the old seed, which is what "copies already made stay as they are"
means for a visitor mid-demo. A new workspace-owned table must be added to `COPIED` or `SKIPPED`
the day it is created, or the catalog test fails.
