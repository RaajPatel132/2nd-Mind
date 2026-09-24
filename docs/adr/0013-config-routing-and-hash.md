# ADR-0013: Config from env only; per-step routing, prices and a config hash

- **Status:** accepted
- **Date:** 2026-09-24

## Context

FR-14.2 (switch a step by config), FR-14.5 (versioned prices), FR-19.2 (every turn records
the config it ran with), NFR-9.4 (typed, validated, env-only config).

## Decision

- **Settings:** `pydantic-settings`, environment only (no `.env` loading in the app).
  Validation errors name the variable and stop start-up. Production refuses `DEV_AUTH`,
  content logging and fake providers.
- **Routing:** `backend/config/models.yaml` gives each step a provider, model, optional
  fallback, timeout, output limit and effort. `MODEL_<STEP>` / `MODEL_<STEP>_FALLBACK`
  override from env. `MODEL_PROVIDER_MODE`: `auto` (no key → use the fallback if it has one,
  else the fake), `live` (missing key → refuse to start), `fake` (all fake: tests, E2E).
- **Prices:** `backend/config/prices.yaml`, versioned; every routed model must be priced.
- **Config hash:** sha256 over resolved routing, the price table and prompt file hashes;
  recorded on every turn and exposed on `GET /v1/meta`. Secrets are excluded.

The YAML lives under `backend/config/` so it ships inside the image with the code it
configures.

## Alternatives considered

- **Routing in env vars only:** unreadable for eight steps; YAML plus env overrides keeps the
  default visible and reviewable.

## Consequences

`make up` works with no keys (fake mode) and upgrades itself when one key is added.
