# ADR-0034: Web hardening and staging access

- **Status:** accepted
- **Date:** 2026-09-29
- **Amends:** ADR-0016 (dev auth; adds the CSRF check it left as a follow-up)

## Context

Staging is on the public internet behind a load balancer, with real provider keys, before real
accounts exist (S6). A browser is the client, cookies carry the session, and the API sits behind
the same origin as the SPA. That makes a few classic web risks ours to close, and dev sign-in
cannot be open to anyone who finds the URL.

## Decision

**Headers.** nginx (the web tier) sends a strict `Content-Security-Policy` (`default-src 'self'`,
`frame-ancestors 'none'`, and only the fonts we serve), `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy` and `Permissions-Policy` on every response, hashed
assets included (a location's own `add_header` had been dropping them). The API sends
`default-src 'none'`, `no-store`, and the same three, on every response including its errors;
HSTS goes out wherever cookies are Secure (`NGINX_HSTS`, and the API when
`SESSION_COOKIE_SECURE`). The interactive docs get no CSP, since Swagger UI loads its own assets.

**CORS is off.** The SPA and the API share an origin (nginx now, CloudFront later), so no
browser has a reason to call the API from elsewhere. There is no CORS middleware, and a
preflight gets no `Access-Control-*` headers. A second front end would be added by name in
`ALLOWED_ORIGINS`, for the same-origin check below.

**CSRF.** `SameSite=Lax` stays. On top, every write (POST, PUT, PATCH, DELETE) is refused
before a handler runs when the browser says it came from another site (`Sec-Fetch-Site` other
than `same-origin`) or its `Origin` isn't this site's or an allowed one, and every write with a
body must be `application/json`, so an HTML form can't submit one. A request with neither
header isn't from a browser (a script, curl, the E2E runner), and CSRF needs a browser with a
cookie, so it passes on to the session check.

**Sizes.** nginx refuses a body over `NGINX_MAX_BODY` (1 MB); the API refuses more than
`MAX_REQUEST_BYTES` (256 KB), by `Content-Length` and, for a chunked body, by reading it up to
the limit. `MAX_MESSAGE_CHARS` still applies inside that.

**Exposure by environment.** `/docs/api` and the OpenAPI schema exist in development and staging
and not in production; `/design` is served by nginx unless `NGINX_DESIGN=off`; `/v1/dev/*`
exists only where `DEV_AUTH` does, and production refuses `DEV_AUTH` at start-up.

**Staging access.** With `ENV=staging`, dev sign-in needs `STAGING_ACCESS_CODE`, compared in
constant time (always compared, so the time says nothing), attempts limited per address
(`LOGIN_ATTEMPTS_PER_MINUTE`, a Redis bucket; over it, `429`). Each holder signs in with an email
and gets their own user and private workspace, so isolation between them is the same as between
accounts. Start-up refuses `ENV=staging` with `DEV_AUTH=true` and no code. Real sign-in
replaces all of this in S6.

**Start-up guards** for staging and production: a `SESSION_SECRET` that isn't one of the example
values, `SESSION_COOKIE_SECURE=true`, `MODEL_PROVIDER_MODE=live`, tracing either configured or
explicitly off, and no content logging.

## Alternatives considered

- **CORS with an allowlist:** more moving parts for a topology that never needs it.
- **CSRF tokens:** a custom header and a token endpoint for what `Sec-Fetch-Site` and `Origin`
  already tell a modern browser's server. Revisit if a client without those headers matters.
- **HTTP basic auth at the load balancer for staging:** hides the whole site, including
  health checks, and gives no per-person isolation.

## Consequences

A browser too old to send `Sec-Fetch-Site` still sends `Origin` on a write, so it is checked
too. A non-browser client that adds a wrong `Origin` is refused, which is the point. The access
code is one shared secret: fine for a handful of invited people, not a substitute for accounts.
