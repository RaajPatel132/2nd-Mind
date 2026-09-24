# ADR-0014: Prompts are versioned files; released versions are immutable

- **Status:** accepted
- **Date:** 2026-09-24

## Context

FR-19.1: prompts are versioned artefacts, not inline strings; eval numbers must be traceable
to exact prompt versions (FR-19.3).

## Decision

`backend/prompts/<id>/v<N>.md` with YAML frontmatter (`id`, `version`, `step`,
`description`, `variables`). A loader validates that placeholders match the declared
variables and renders with a typed Pydantic variables model. `prompts.lock.json` pins the
sha256 of every released file: a unit test fails if a locked file changes, or if a new file
isn't locked. `python -m secondmind.config.prompt_lock` appends new versions and refuses to
rewrite existing ones. Routes name their prompt (`answer@1`), so switching versions is config.

## Alternatives considered

- **Jinja templates:** more power than we need, and logic in prompts is harder to review.
- **A prompt management SaaS:** versions would live outside the repo and the eval stamp.

## Consequences

Changing a prompt is always a new file plus a routing change, which shows up in the config
hash and on every turn that uses it.
