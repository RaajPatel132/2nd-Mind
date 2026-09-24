# ADR-0003: Workspace isolation by scoped repositories plus Postgres row-level security

- **Status:** accepted
- **Date:** 2026-09-24

## Context

"Nothing is ever read or written across workspaces" (§6.5, NFR-2.1) and it must be provable
(NFR-2.2). Scoping in application code alone fails silently the first time someone writes a
query without a `WHERE workspace_id = …`.

## Decision

Two layers, from the first table:

1. **Repositories need a `WorkspaceScope` to exist.** Domain code gets stores from a factory
   that takes the scope; there is no unscoped query API in domain code.
2. **RLS as the backstop.** Every workspace-owned table has `workspace_id` and a policy
   `workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid` for
   `USING` and `WITH CHECK`. The app connects as `secondmind_app`, a login role that is a
   member of `secondmind_rw`: not the owner, not superuser, no `BYPASSRLS`. Each transaction
   runs `set_config('app.workspace_id', …, true)`. No setting means NULL, which means zero
   rows, not all rows.

Integration tests prove, per table, that workspace B cannot read, update, delete, insert into
or move rows of workspace A, through repositories **and** raw SQL on the app role. A catalog
test fails if any table with `workspace_id` lacks RLS. Readiness fails if the app role could
bypass RLS.

## Alternatives considered

- **Application scoping only:** no backstop; one missed filter leaks data.
- **Schema or database per workspace:** strong isolation but thousands of schemas, painful
  migrations, and cross-workspace admin queries become hard.

## Consequences

Costs: one extra `SELECT set_config(...)` per transaction; migrations must run as a
different role (the owner, via `DATABASE_MIGRATION_URL`); admin/quota queries that span
workspaces (S4) need an explicit, audited path (a separate role or `SECURITY DEFINER`
function). Transaction-local settings keep pooled connections safe. `users` and `workspaces`
are identity tables, not workspace-owned, and are reached only through the `auth` module.
