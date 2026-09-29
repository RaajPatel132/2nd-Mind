# ADR-0033: The Postgres features we rely on, checked against RDS Postgres 16

- **Status:** accepted
- **Date:** 2026-09-28

## Context

Everything runs on one Postgres store (ADR-0002) with row-level security (ADR-0003). Locally that
is the `pgvector/pgvector:pg16` image with a superuser; on AWS it will be RDS Postgres 16
(ADR-0012), where nobody is a superuser: the master user is a member of `rds_superuser`, and
the schema owner we create is an ordinary role. A feature that only works as a superuser would
surface on deploy day. This records every feature we use and what RDS needs for it.

## Decision

We use only the features below, all available on **RDS Postgres 16.5 or later** (pgvector 0.8):

| Feature | Where | On RDS |
|---|---|---|
| `vector` extension (pgvector): `vector(1536)`, cosine distance `<=>` | `memory_keys`, `conversation_keys`, the soft channel and `search` | Created **once by the master user** (`CREATE EXTENSION vector`); migration 0002's `CREATE EXTENSION IF NOT EXISTS` is then a no-op for the owner |
| `halfvec` and HNSW with iterative scans (at scale, ADR-0024) | not yet | pgvector ≥ 0.8 |
| B-tree indexes, unique and expression (`lower(email)`), partial (`WHERE state = 'pending'`) | every table | core |
| GIN on a stored generated `tsvector` (`to_tsvector('english', text)`) | keys, conversation keys | core |
| Full text: `to_tsquery('english', …)`, `ts_rank_cd(…, 32)`, `@@` | lexical search | core |
| `jsonb`, `uuid`, `timestamptz`, `numeric`, identity columns | throughout | core |
| `gen_random_uuid()` | the `self` entity trigger | core (Postgres 13+) |
| Row-level security: `ENABLE ROW LEVEL SECURITY`, one `workspace_isolation` policy per workspace-owned table, keyed on `current_setting('app.workspace_id', true)` | every workspace-owned table | core; the owner creates the policies |
| `set_config(…, true)` for the workspace scope and `statement_timeout` per transaction | `Database.workspace` | core |
| A `SECURITY DEFINER` trigger function, `search_path` pinned, `EXECUTE` revoked from `PUBLIC` | `create_self_entity` | core; owned by the schema owner |
| Roles: the schema owner has **`CREATEROLE` only** (no superuser, no `BYPASSRLS`); it creates the `secondmind_rw` group role (`NOLOGIN`) and the app login role (`NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`) and grants membership | `bootstrap_role`, migration 0001 | Postgres 16 gives the creator `ADMIN OPTION` on roles it creates, so the owner can grant them without superuser |
| Session advisory lock around migrations and role bootstrap | `migrations/env.py`, `bootstrap_role` | core |

The readiness check (`/readyz`) fails if the app's role is a superuser or can bypass RLS, so a
misconfigured database can't serve traffic. `tests/integration/test_rds_like.py` runs the role
bootstrap and every migration as a non-superuser owner with only `CREATEROLE`, the extension
created beforehand by a separate "master" role, and a second `migrate` started at the same time
(it waits on the lock, then finds nothing to do).

Not used, and not to be introduced without revisiting this record: extensions other than
`vector` (`pg_trgm`, `unaccent`, `pg_cron`), `FORCE ROW LEVEL SECURITY` (the app never owns
tables), logical replication, `COPY … FROM PROGRAM`, untrusted procedural languages, and
anything that needs `rds_superuser` after the first deploy.

## Alternatives considered

- **Aurora Postgres Serverless v2:** scales to zero, but its minimum capacity costs more than a
  `db.t4g.micro` for a demo's load, and pgvector versions arrive later.
- **Letting migrations create the extension:** needs `rds_superuser` for the migrate task on
  every deploy; creating it once as the master user keeps the task's role small.

## Consequences

The first deploy of an environment has one manual database step (create the extension and the
owner role as the master user), written in the runbook. A migration that needs anything outside
the table above fails the RDS-like test before it reaches AWS.
