# Architecture decision records

One page each: context, decision, alternatives, consequences. New decisions copy
[`template.md`](template.md). Superseded records stay, marked with their successor.

| # | Decision | Status |
|---|---|---|
| [0001](0001-python-fastapi-pydantic-uv.md) | Python 3.12, FastAPI, Pydantic v2 and uv for the backend | accepted |
| [0002](0002-postgres-single-store.md) | One Postgres 16 store (pgvector) for relational, vector and lexical data | accepted |
| [0003](0003-workspace-isolation-rls.md) | Workspace isolation: scoped repositories plus row-level security | accepted |
| [0004](0004-sqlalchemy-alembic.md) | SQLAlchemy 2 (async) and Alembic | accepted |
| [0005](0005-langgraph-thin-graph.md) | LangGraph for the turn graph; logic in plain modules | accepted |
| [0006](0006-provider-layer.md) | Our own provider interface over Anthropic and OpenAI, plus a fake | accepted |
| [0007](0007-redis-arq.md) | Redis with arq for background jobs | accepted |
| [0008](0008-langfuse-tracing.md) | Self-hosted Langfuse; trace id = turn id | accepted |
| [0009](0009-react-vite-generated-client.md) | React + Vite SPA using only the generated /v1 client | accepted |
| [0010](0010-test-pyramid.md) | pytest, testcontainers and Playwright | accepted |
| [0011](0011-import-boundaries.md) | Module boundaries enforced with import-linter | accepted |
| [0012](0012-deploy-aws.md) | AWS ECS Fargate, Terraform, GitHub OIDC | proposed |
| [0013](0013-config-routing-and-hash.md) | Env-only config; per-step routing, prices and a config hash | accepted |
| [0014](0014-prompt-registry.md) | Versioned, immutable prompt files | accepted |
| [0015](0015-turn-events-and-streaming.md) | Typed turn events as source of truth; SSE streaming | accepted |
| [0016](0016-dev-auth.md) | Dev-only auth with a signed cookie | accepted |
| [0017](0017-container-images.md) | Slim non-root images; one image for api and worker | accepted |
