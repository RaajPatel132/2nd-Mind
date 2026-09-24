# ADR-0016: Dev-only auth with a signed cookie until real auth

- **Status:** accepted
- **Date:** 2026-09-24

## Context

S1 needs a user and a workspace to exercise isolation end to end, but real sign-up arrives
in S4/S6.

## Decision

`DEV_AUTH=true` enables `POST /v1/auth/dev-login`, which creates or reuses a dev user and
their single private workspace and sets `sm_session`: an HMAC-SHA256-signed, expiring token
(`HttpOnly`, `SameSite=Lax`, `Secure` when configured). Every other endpoint requires the
cookie, and workspace access checks ownership (another user's workspace is 404). Start-up
refuses `DEV_AUTH=true` with `ENV=production`.

## Alternatives considered

- **No auth in S1:** isolation couldn't be exercised through the API.
- **Pulling real auth forward:** out of scope; the token format is versioned (`v1.`) so
  real sessions can replace it cleanly.

## Consequences

`SameSite=Lax` plus JSON-only write endpoints limit CSRF for now; real auth adds CSRF tokens
and server-side revocation.
