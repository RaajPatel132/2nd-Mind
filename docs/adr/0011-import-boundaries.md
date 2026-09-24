# ADR-0011: Module boundaries enforced with import-linter

- **Status:** accepted
- **Date:** 2026-09-24

## Context

NFR-9.1–9.3: modular by domain, dependencies point inward, boundaries enforced by tooling.

## Decision

One top-level package per domain under `secondmind/`, plus `config` and `core`. Four
contracts run in `make check` and CI:

1. `core` imports nothing from other modules and no I/O libraries.
2. Only `api` imports `fastapi`/`starlette`.
3. `sqlalchemy`, `asyncpg`, `alembic`, `anthropic`, `openai`, `redis`, `arq`, `langfuse` are
   imported only from `*/adapters/` (or `api`).
4. A custom `public_interface` contract (`backend/tools/import_contracts.py`): across modules,
   code may import only `secondmind.<m>` or, from composition roots and adapters,
   `secondmind.<m>.adapters`. Anything deeper is internal.

## Alternatives considered

- **Convention and review only:** erodes under deadline pressure.
- **Separate installable packages per module:** heavier tooling for the same guarantee.

## Consequences

Composition happens in `api` (and the worker entrypoint), the only places that build
adapters. Moving code between modules sometimes means adding an export to an `__init__`.
