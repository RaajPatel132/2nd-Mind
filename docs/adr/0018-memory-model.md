# ADR-0018: Memory model: closed kinds, entities and three clocks

- **Status:** accepted
- **Date:** 2026-09-25

## Context

The write side needs a schema that recall (S3), links and files (S4) and patterns (S9) can
build on without re-extracting every stored memory. The earlier plan had 13 item types. They
mixed what a memory *is* ("birthday", "recipe") with how it *behaves over time*, so "I moved
to Pune" and "watched Severance" had nowhere to go but an overwrite or a duplicate. People
had their own table, while places, shows and projects had none.

## Decision

- **Kind is closed, topic is open.** A memory has one of 10 `kind`s, chosen by lifecycle:
  fact, preference, episode, plan, task, intention, resource, note, rule, pattern. A CHECK
  constraint allows only that kind's `state`s (fact `current → superseded`, intention
  `wanted → active → fulfilled | dropped`, …). Topics are a free `subtype` plus a category
  path, both normalised per workspace. `state` is about the world; `status`
  (active / archived / deleted) is about the record, and deletes are soft.
- **Entities.** People, places, orgs, things, works, topics, projects and lists are rows in
  `entities` (exactly one `self` per workspace, created by a trigger on `workspaces`).
  Memories point at them through `memory_entities` with a role (`about`, `with`, `for`, `by`,
  `at`, `owner`, `part_of`). Entity-to-entity facts go in `entity_relations`, and
  item-to-item links (`supersedes`, `fulfils`, …) go in `memory_links`.
- **Three clocks.** `mentioned_at`; `occurred_start/end` plus `time_precision` and an RRULE;
  `due_at`; and `valid_from/valid_to`. A partial index on (subject, predicate) where
  `valid_to IS NULL` makes "the current value" one indexed lookup.
- **Layers.** `in_core` and `in_quick` (with a reason and an until) are flags. The archive is
  implicit: every active item is in it.
- **Change is history.** Every write goes through one `MemoryWriter` per turn, which records
  primitive write-log rows (item, entity, link, role, relation, trigger) with before/after
  snapshots, and a full `item_versions` snapshot. Undo replays the log backwards as a new turn.
- **Search lives in `memory_keys`** (ADR-0020). Items have no search columns.
- Every table is workspace-owned under RLS, and the isolation tests cover each one.

## Alternatives considered

- **Keep typed items (13 types).** Types encode topic, not behaviour, and every new topic
  would need a new type.
- **A people table now, other entity kinds later.** That means a second migration plus
  re-extraction when places and works arrive in S3/S4.
- **Overwrite on change, keep an audit table.** Recall couldn't answer "where did I live
  before?", and undo would have to reconstruct state.

## Consequences

Supersede and fulfil are cheap and reversible, and recall can filter by kind, state, clock
and entity. The price is a wide `memory_items` row, a CHECK constraint to keep in step with
`core/memory_model.py`, and extraction prompts that must pick a kind and state correctly
(the golden cases measure this). A new kind means a migration, which is on purpose.
