# ADR-0035: One environment on one host, built to move

- **Status:** accepted (S4; the parts that need an AWS account are checked when the guide is followed)
- **Date:** 2026-09-30
- **Supersedes:** the topology of ADR-0012 (its region, arm64 and Langfuse Cloud choices carry over)
- **Amends:** ADR-0034 (code sign-in is allowed in production until S6)

## Context

The audience is a handful of technical visitors who try the app once. ADR-0012 planned two AWS
environments on Fargate, RDS, ElastiCache, an ALB and CloudFront: about $80 a month each, for
traffic that doesn't need it, and almost none of it would move to another host. The owner has read
about AWS but never used it, so the deployment is also a course in DevOps. The money target is no
cash beyond the domain: AWS's new-account credits ($200) pay while the owner learns, and Oracle
Cloud's Always Free tier pays after.

## Decision

**One environment, production.** What stands in for staging is the local rehearsal
(`make up-prodlike`: the same compose file and images, real keys, run before a merge) and
production itself while it's behind the access code. `ENV=production` everywhere; `ENV=staging`
no longer loads. Sign-in by code (`DEV_AUTH` with `ACCESS_CODE`) is allowed in production until
S6's accounts replace it; open dev sign-in and the `/v1/dev` helpers exist only in development.
`GUESTS_OPEN` (off) keeps every way in behind the code until S5. A second cloud environment is
added when traffic or risk justifies its cost, not before.

**One always-on host.** An EC2 `t4g.small` (2 vCPU, 2 GB, arm64, Ubuntu 24.04 LTS) in one AZ of
`us-east-1`, with an Elastic IP, running Docker Compose from one file, `compose.prodlike.yaml`:
Caddy (TLS from Let's Encrypt, routing by host name), the web tier (nginx: SPA and `/v1`/`/readyz`
proxy), api, worker, Postgres 16 with pgvector and Redis. Postgres, Redis and Caddy's certificates
live on a separate encrypted volume that outlives the instance. Ubuntu, not Amazon Linux, so one
host script runs unchanged on Oracle or a VPS. Before launch the host can be stopped
(`make host-stop`); after launch it can't sleep.

**Sizing, by measurement.** The stack's container memory limits add up to about 1.6 GB (api 448 MiB,
worker 288, Postgres 448, Redis 96, web 48, Caddy 64, and the one-off migration 256), and the swap
file (2 GB) is a cushion, not working memory. On the rehearsal stack, with production's settings,
the `@prodlike` subset (save, recall, undo, the glass box, the kill switch) peaked at **611 MiB** for
all containers together (api 305, worker 179, Postgres 118, Caddy 32, Redis 8, web 7), about 1.4 GB
under the host's memory. `make measure-prodlike` repeats it. If it stops fitting, trim before
resizing: worker concurrency, Postgres memory settings, api workers. A bigger instance is a question,
because it may not be allowed on the Free plan and draws more credit.

**The edge.** Caddy terminates TLS and routes by host name; each project drops its own site file into
`infra/edge/sites/`. It compresses everything except `/v1` (a compressing proxy would hold back the
event stream), flushes immediately, and makes a deploy a blip: it health-checks the web tier's
`/readyz` every second, marks it down while the api restarts, and holds requests up to 15 s.

**Portable by construction.** The containers, the compose file, Caddy, the host script, the backup
format, the images (GHCR, public) and DNS (Cloudflare, DNS only) know nothing about AWS. AWS appears
only in `infra/terraform/`, `infra/host/aws/` (the AWS half of the host's setup, deploy and backup)
and CI's AWS steps; tests keep it so. Containers hold no AWS credentials: the host reads SSM and
writes the backups, and IMDSv2 has a hop limit of 1, so a container can't reach it.

**Deploys.** CI builds and scans arm64 images, and pushes them to GHCR tagged by SHA (never
overwritten). The deploy workflow (environment `production`, one at a time) assumes a role through
GitHub OIDC and sends the `secondmind-deploy` SSM document, which checks out the SHA and runs
`infra/host/deploy.sh`: fetch the env file, pull, migrate as a one-off, start on the new tag, wait for
`/readyz` to answer from this release. A failed migration changes nothing; a release that doesn't
become ready is replaced by the previous one (both tried, see runbook §5). A rollback is the same
script with `--no-migrate`, safe because a release runs on the schema of the one before it
(NFR-10.4). The deploy role can send that document to instances tagged `project=secondmind`, and
nothing else; it trusts only the `production` environment of this repository.

**Backups and patching.** Daily snapshots of the data volume (7 kept, Data Lifecycle Manager), a
nightly `pg_dump` kept two days on the host and 30 in a private, versioned S3 bucket (the host
uploads; containers can't), and a restore to the local stack that was tried (runbook §7).
`unattended-upgrades` installs security updates; a timer reboots in a weekly window only when an
update needs it.

**Costs.** About **$18 a month**, drawn from the credits: the instance about $12.3, its public IPv4
address $3.65, disks about $1.8, snapshots about $0.5, the bucket and logs cents. $200 lasts about
11 months at that rate, about $0.60 a day. The Free plan closes the account at month 6, or sooner
if the credits run out, so **by month 5 the app moves** (ledger 51) or the account is upgraded to
the Paid plan, which keeps the leftover credit to month 12. Two Budgets watch it: $1 a month of
net cost (credits counted: the "no cash" alarm) and $25 of gross cost (credits not counted: the
"draining faster than planned" alarm). Stopped, the instance stops drawing; the address and disks
keep drawing about $5.60 a month.

| Month | Draw if always on | Credit left of $200 |
|---|---:|---:|
| 1 | $18 | $182 |
| 3 | $18 | $146 |
| 5 | $18 | $110 |
| 6 | $18 | $92: the Free plan closes here unless the account is upgraded |
| 11 | $18 | about $2 |

**The move** swaps only these: EC2 → an Ampere A1 instance (arm64), Elastic IP → a reserved public
IP, SSM parameters → a root-only env file (`ENV_SOURCE=file`), SSM deploys → SSH from CI with a
deploy key, snapshots and S3 dumps → block-volume backups and object storage, Terraform's AWS
provider → the OCI provider. The fallback is a small ARM VPS at about ₹400 to ₹600 a month (an x86
one needs a `linux/amd64` image too). The guide's Part 4 is the checklist.

## Alternatives considered

- **ADR-0012's topology, once:** about $80 a month, and most of its parts (ALB, managed Redis and
  Postgres, CloudFront) are AWS-only, so none of it moves.
- **CloudFront in front of the host:** it would still need a trusted certificate on the host, a second
  host name and headers set in two places. It can be added later without touching the app.
- **Two environments:** twice the cost, and nobody to protect at this traffic.
- **Amazon Linux:** a smaller gap to AWS, a bigger one to every other host.
- **ECR:** GHCR is free for a public repo's packages, needs no pull credentials and is where the images
  stay after the move. The registry concepts carry over.
- **A deploy workflow that builds its own images:** simpler, but then the image that was tested and
  scanned isn't the one that ships. CI pushes the image it scanned.

## Consequences

One host is one failure domain: a bad deploy or a dead instance is an outage until the previous tag or
a restored volume is back. That is accepted at this audience, and the runbook covers both. The edge
holds requests 15 s, which covers a normal deploy but not a long outage, when visitors get Caddy's
503. A workflow can only be dispatched by hand once it is on the default branch, so before the sprint
merges a rollback goes through `make prod-deploy` (SSM directly) and the first deploy comes from a
push to a sprint branch. Revisit when there is a second project on the domain (the `platform/`
Terraform root is built to be shared), real traffic, or an availability target.
