# Security

## Reporting a problem

Please use GitHub's private vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**. If that isn't available to you, open an issue that says you have a
security report and asks for a private channel, without the details in it.

A report that includes how to reproduce it, and which commit it was found on, can be acted on
fastest. Only the `main` branch is supported.

## What has been built to hold

The page [`docs/architecture.md`](docs/architecture.md#where-trust-is-enforced) lists each concern,
how it is held and the test that proves it. In short:

- **Isolation between people**: scoped repositories plus Postgres row-level security on every
  owned table, tried through raw SQL as well as through the code
  ([ADR-0003](docs/adr/0003-workspace-isolation-rls.md)).
- **Pasted links and page text**: one fetch door that pins the address it checked, and content
  treated as data that a model may never act on
  ([ADR-0036](docs/adr/0036-link-fetching-and-fetch-safety.md),
  [ADR-0037](docs/adr/0037-content-is-data.md)).
- **Secrets**: refused by the write policy rather than stored; message content is never logged
  ([ADR-0021](docs/adr/0021-sensitivity-and-secrets.md)).
- **The web tier**: strict CSP, no CORS, cross-site writes refused, size limits
  ([ADR-0034](docs/adr/0034-web-hardening-and-staging-access.md)).
- **Spend**: a gate before every turn with a kill switch, caps and quotas
  ([ADR-0032](docs/adr/0032-spend-safety.md)).
- **The repository**: gitleaks runs over the full history in CI and on every commit, and
  dependencies and images are audited.

## Out of scope

Findings about the synthetic sample data, about an operator's own provider keys or host
configuration, and denial of service by volume against a single small host.
