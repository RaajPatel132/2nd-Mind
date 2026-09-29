# Contributing

## Setup

```bash
npm install          # repo root: installs git hooks (husky) and commitlint
make install         # backend (uv) and frontend (npm) dependencies
make check           # everything CI runs, except E2E
```

Hooks run on every commit: `pre-commit` (ruff lint + format, gitleaks, hygiene checks on
staged files) and `commit-msg` (the commit standard below). Don't bypass them with `--no-verify`.
CI runs the same checks again, so a bypassed commit fails there.

## Commit messages

We follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/), checked
by [commitlint](https://commitlint.js.org) using `commitlint.config.mjs`. The goal is a history you
can skim: **one short line per commit that says what is now true**, not what you typed.

```
<type>(<scope>): <outcome, imperative, lower case, no full stop>

[optional body: up to 6 short lines on why, not how]

[optional footer: BREAKING CHANGE: …, Refs: S1.4]
```

| Rule | Limit | Why |
|---|---|---|
| Header | ≤ 72 chars | Fits `git log --oneline`, GitHub and terminal views untruncated |
| Subject | imperative, ≥ 10 chars, no trailing `.` | Reads as "this commit will …" |
| Subject | states the **outcome** | `stream replies over SSE`, not `update sse.py` / `wip` / `fixes` |
| Body | optional, ≤ 6 lines, ≤ 100 chars each | Why the change was needed; the diff already shows how |
| Scope | one of the module / area names below | Groups history by module |
| Trailers | no `Co-authored-by:`, no "Generated with …" badges | Commits are attributed to the author only |

**Types:** `feat` (user-visible capability), `fix` (bug fix), `perf`, `refactor` (no behaviour
change), `test`, `docs`, `build` (deps, packaging, images), `ci`, `chore` (repo upkeep),
`style` (formatting only), `revert`.

**Scopes:** backend modules (`api`, `agent`, `ingestion`, `retrieval`, `memory`, `providers`,
`policy`, `metering`, `auth`, `jobs`, `observability`, `evals`, `config`, `core`, `db`,
`prompts`), delivery (`web`, `e2e`, `infra`, `docker`, `ci`, `deps`) and docs/repo (`docs`,
`adr`, `sprint`, `repo`, `release`). Omit the scope when a change spans many modules.

**Breaking changes:** `feat(api)!: rename turns endpoint` plus a `BREAKING CHANGE:` footer.

**Story references:** put the story id in a footer (`Refs: S1.6`), not in the subject.

Good:

```
feat(providers): use the fallback model when the breaker is open
fix(api): close the SSE stream when the client disconnects mid-turn
test(db): prove RLS hides rows across workspaces through raw SQL
build(docker): run api and worker images as a non-root user
```

Rejected by the hook:

```
Updated stuff.                               # no type, vague, past tense, full stop
feat: added provider router                  # past tense
fix(api): fixes                              # says nothing about the outcome
chore: wip                                   # vague
feat(api): add turns endpoint                # fine header, but…
                                             # …a body of three paragraphs is rejected
Co-authored-by: …                            # attribution trailers are not used
```

Keep commits small and cohesive: one outcome per commit. If the subject needs "and", it's
probably two commits.

## Changing the database schema

A deploy is rolling: for a while the old code runs against the new schema, and the new code may
have to run against the old one after a rollback. So a column or table is never dropped or
renamed in the same release that stops using it.

1. **Release N** stops reading and writing the column (and adds its replacement, if any).
2. **Release N+1** drops or renames it. Its migration sets a module constant `CONTRACT_AFTER = "<revision of release N's last migration>"`.

`backend/tests/unit/test_migration_compat.py` fails a migration whose `upgrade()` drops or
renames without that constant. Adding columns, tables and indexes needs nothing. Migrations run
as the schema owner behind an advisory lock, so two started together take turns; the RDS-like
test (`tests/integration/test_rds_like.py`) runs them as a non-superuser owner.

## Definition of done

1. Acceptance criteria met and shown by tests (or the sprint report says how it was checked).
2. `make check` is green: lint, types, unit and integration tests, import boundaries.
3. No real personal data anywhere. Fixtures are synthetic.
4. A real decision (library, schema shape, trade-off) gets a short ADR in `docs/adr/`.
5. No secrets in the repo; `.env.example` lists every variable.
6. UI follows `docs/design/system.md` (Ink): tokens, primitives and the checklist in §13.
