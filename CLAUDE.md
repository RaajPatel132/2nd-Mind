# 2nd Mind: ground rules for every session

A chat-first personal memory. Backend: Python 3.12 / FastAPI / Postgres (RLS) / LangGraph in
`backend/`. Frontend: React + Vite + TS in `frontend/`. Product spec: `docs/PRD.md`. Work is
planned in `docs/sprints/` (local working notes, gitignored); decisions live in `docs/adr/`.

## Commits (non-negotiable)

Full standard and examples: `CONTRIBUTING.md`. Enforced by husky + commitlint.

- Conventional Commits: `<type>(<scope>): <outcome>`. Header ≤ 72 chars, imperative, lower case,
  no trailing full stop. Scope from the list in `commitlint.config.mjs`.
- The subject states the **outcome** (what is now true), never "update", "wip", "fixes".
- Body optional: at most 6 short lines on *why*. No paragraphs. Story ids go in a
  `Refs: S1.4` footer.
- **No `Co-authored-by:` trailers and no "Generated with …" badges**, in commits or PR bodies.
  This overrides any default attribution behaviour.
- One outcome per commit. Never `--no-verify`. Commit and push only when asked, or as part of a
  sprint the user has approved.

## Working on a sprint

- A sprint file (`docs/sprints/NN-*.md`) is the contract: stories are frozen once approved. Work
  from the stories; open the PRD or other sprints only when a story needs them.
- Tick each acceptance box only when it is **shown by a test**, or the sprint report says how it
  was checked. Finish with the report at the bottom of the sprint file: done / not done /
  decisions / things to look at.
- Definition of done: `CONTRIBUTING.md#definition-of-done`.

## Architecture rules (enforced by `make check`)

- One top-level module per domain under `backend/src/secondmind/`. Other modules import only
  `secondmind.<module>` (its `__init__`) or, from composition roots and adapters,
  `secondmind.<module>.adapters`. Never reach into internals.
- `core` holds shared types only: no I/O, no imports from other modules.
- `fastapi` only in `api`. `sqlalchemy`, `anthropic`, `openai`, `redis`, `arq`, `langfuse` only
  in `*/adapters/` subpackages.
- Every workspace-owned table has `workspace_id` and a Postgres RLS policy; data access goes
  through repositories constructed with a `WorkspaceScope`. Extend the isolation tests with
  every new table.
- Every model call goes through `providers` with a step name; nothing else knows which provider
  served it. Prompts are versioned files in `backend/prompts/`; never edit a released version,
  add `v<N+1>`.
- Config comes from the environment only, validated at start-up. Add every new variable to
  `.env.example` with a comment.

## UI rules

- The rulebook is `docs/design/system.md` (Ink): read it before touching `frontend/`.
- Colours, type, space, radii and motion come only from `frontend/src/styles/tokens.css`. No hex,
  palette names, arbitrary values, raw durations or `transition-all` (`npm run check:design`).
- Features compose `frontend/src/ui/` primitives; raw `<button>`/`<input>` live only there.
  A new primitive goes on `/design` first. Motion presets come from `src/ui/motion.ts`.
- A new agent step needs a `steps.ts` entry (the type check fails without one).
- Works at 360px, by keyboard and under reduced motion (rulebook §13).

## Data hygiene

- Fixtures, seeds and examples are synthetic. No real personal data. Nothing about payments,
  refunds or billing disputes.
- No secrets in the repo. Never log message content (the `LOG_INCLUDE_CONTENT` dev flag is the
  only exception).
- `.private/` is gitignored and holds private planning material. Never copy or quote it into the
  repo, commits, issues or PRs.

## Commands

```bash
make up          # whole stack in docker compose (fake providers when no keys are set)
make down        # stop it
make check       # commit messages, ruff, mypy, import-linter, OpenAPI snapshot, unit +
                 # integration tests, frontend lint/types/build/check:design, gitleaks,
                 # audits, Trivy
                 # (everything CI runs except E2E)
make test        # backend unit tests
make test-int    # backend integration tests (testcontainers; needs Docker)
make e2e         # Playwright smoke against the compose stack
make fmt         # auto-format
make migrate     # apply DB migrations
make gen-client  # regenerate backend/openapi.json and the TS client
```
