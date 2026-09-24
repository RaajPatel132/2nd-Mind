# ADR-0005: LangGraph for the turn graph; the logic lives in plain modules

- **Status:** accepted
- **Date:** 2026-09-24

## Context

A turn becomes a pipeline with branches: intent → save and/or recall → answer, with
corrections and held writes later (S2, S3). We want explicit state, streaming, and a shape
new steps can plug into without rewriting the runner.

## Decision

The turn is a LangGraph `StateGraph` (`intent` → `answer` in S1) whose nodes are thin: they
read collaborators from a typed runtime context (`TurnContext`) and call plain modules
(`providers`, later `ingestion`, `retrieval`, `memory`). Tokens leave through LangGraph's
custom stream writer. Persistence, metering and tracing stay in our `TurnRunner`, not in
graph callbacks.

## Alternatives considered

- **Hand-rolled async pipeline:** less dependency surface, but we'd rebuild branching,
  state merging and streaming.
- **LangChain agents / a framework agent loop:** hides decisions we must show in the glass
  box, and ties us to its provider wrappers (we own the provider contract, ADR-0006).

## Consequences

The graph can be swapped or bypassed because nodes are thin. We depend on LangGraph's
runtime-context and stream-writer APIs (pinned via `uv.lock`); upgrades get a CI run.
