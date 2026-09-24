# ADR-0017: Slim, non-root images; one image for api and worker; no BuildKit-only syntax

- **Status:** accepted
- **Date:** 2026-09-24

## Context

NFR-10.9: minimal, non-root images with vulnerability scanning. `make up` must work from a
clean clone on common Docker setups, including colima without the buildx plugin.

## Decision

- **Backend:** multi-stage on `python:3.12-slim-trixie`; uv builds a non-editable venv; the
  runtime stage upgrades OS packages and runs as uid 10001. The same image runs `api`
  (uvicorn), `worker` (arq) and `migrate` (bootstrap + alembic).
- **Web:** Node build stage; runtime `nginxinc/nginx-unprivileged` (uid 101).
- **Portability:** plain Dockerfile syntax, with no `RUN --mount` cache mounts.
- **Scanning:** Trivy in CI and `make scan-images`, failing on fixable CRITICAL findings.

## Alternatives considered

- **Distroless/Chainguard runtime:** fewer CVEs, but a floating Python version on free tags
  and harder debugging.
- **BuildKit cache mounts:** faster rebuilds, but they break builds without buildx.

## Consequences

Rebuilds re-download dependencies when the lockfile changes. `RESOURCES_DIR=/app` tells the
installed package where `config/` and `prompts/` are.
