# 2nd Mind

A chat-first personal memory. Tell it anything (a show to watch, a gift idea, a deadline, a
link) and it decides what the thing is, when it matters, who it concerns and where to keep it.
Ask for anything back by meaning, time, person or category. Every decision it makes is visible
in a **glass box** beside the chat and can be inspected and corrected. Models sit behind a
provider-agnostic layer (Anthropic and OpenAI today, switchable per step by config), every turn
is traced, and every token is metered.

> Status: early development (walking skeleton). The product spec lives in
> [`docs/PRD.md`](docs/PRD.md).

## Run it locally

Requirements: Docker (with Compose v2), `make`. For development outside containers: `uv` and
Node 22+.

```bash
cp .env.example .env     # optional: add ANTHROPIC_API_KEY and/or OPENAI_API_KEY
make up                  # builds and starts everything, runs migrations, prints the URLs
```

With no provider keys the app runs in **fake-provider mode** (deterministic canned replies), so
everything works offline. `make down` stops the stack.

## Module map

The backend (`backend/src/secondmind/`) has one package per domain. Each exposes a small typed
interface in its `__init__`; infrastructure code lives in an `adapters/` subpackage.

| Module | Owns |
|---|---|
| `api` | FastAPI routes, schemas, SSE, and the composition root that wires adapters |
| `agent` | The LangGraph turn graph, the turn runner, turn/event storage ports |
| `providers` | The provider-agnostic model interface, per-step router, resilience, fake provider |
| `memory` | Workspace-scoped persistence: RLS-scoped sessions (items arrive in S2) |
| `auth` | Users, workspaces, sessions |
| `metering` | The usage ledger (quotas and spend caps later) |
| `observability` | JSON logs, request/turn context, the tracing port (Langfuse adapter) |
| `jobs` | Background jobs run by the arq worker |
| `config` | Env settings, model routing, prices, prompt registry, config hash |
| `core` | Shared types only: ids, scopes, usage, events, errors |
| `ingestion`, `retrieval`, `policy`, `evals` | Placeholders for the save, recall, write-policy and eval work |

Rules, enforced by `make check` and CI (import-linter, see
[ADR-0011](docs/adr/0011-import-boundaries.md)):

- `core` imports no other module and does no I/O.
- Only `api` imports FastAPI. SDKs and drivers (`sqlalchemy`, `anthropic`, `openai`,
  `redis`, `arq`, `langfuse`) are imported only in `*/adapters/`.
- Modules use each other only through `secondmind.<module>` or, from composition roots and
  adapters, `secondmind.<module>.adapters`. Anything deeper is internal.
- Workspace-owned data is reachable only through repositories built with a `WorkspaceScope`,
  and Postgres row-level security backs that up ([ADR-0003](docs/adr/0003-workspace-isolation-rls.md)).

## Develop

```bash
npm install              # git hooks + commit standard
make install             # backend + frontend dependencies
make check               # everything CI runs, except E2E
make help                # all targets
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the commit standard and definition of done, and
[`docs/adr/`](docs/adr/) for architecture decisions.
