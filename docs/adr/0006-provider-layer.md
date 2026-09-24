# ADR-0006: Our own provider interface over Anthropic and OpenAI (no LiteLLM), plus a fake

- **Status:** accepted
- **Date:** 2026-09-24

## Context

Every model call must report the same usage/cost shape (FR-14.3), be routable per step by
config (FR-14.2), survive provider failures (FR-14.4), and later accept a self-hosted model
(FR-14.7). Tests must be deterministic (NFR-9.5).

## Decision

`providers` defines the contract (streaming chat, structured output, tools, embeddings,
normalised `Usage`) and a `ModelRouter` that callers address **by step only**. Adapters for
Anthropic (Messages API, `messages.parse` for structured output) and OpenAI (Chat
Completions, so any OpenAI-compatible server such as vLLM works via `base_url`) live in
`providers/adapters/`. Cost is computed by the router from `config/prices.yaml`, never by
adapters. A deterministic, scriptable fake provider backs every test and offline dev.

Resilience in the router: per-step timeout (first chunk and between chunks), bounded
full-jitter retries on timeouts/429/5xx/network only, never after tokens reached the user; a
circuit breaker per provider; an optional per-step fallback, recorded on the call.

## Alternatives considered

- **LiteLLM:** fast to start, but its normalisation is the thing we must own and test.
- **Provider SDK retries:** hidden from our metering and glass box; disabled (`max_retries=0`).
- **Anthropic server-side refusal fallbacks:** would switch models inside one call, which
  our glass box and cost table wouldn't see. Not used; our router-level fallback covers
  failures visibly.

## Consequences

Contract tests run one suite over fake (always) and real providers (`pytest -m live`).
Switching a step's model is `MODEL_<STEP>=provider:model`. We maintain two adapters.
