# ADR-0035: One environment on one host, built to move

- **Status:** proposed (S4.1; completed with the measured numbers in S4.5)
- **Date:** 2026-09-30
- **Supersedes:** the topology of ADR-0012

## Context

The audience is a handful of technical visitors who try the app once. ADR-0012 planned two AWS
environments on Fargate, RDS, ElastiCache, an ALB and CloudFront: about $80 a month each, for
traffic that doesn't need it. The owner has read about AWS but never used it, so the deployment
is also a course in DevOps. The money target is no cash beyond the domain: AWS's new-account
credits ($200) pay while the owner learns, and a free host (Oracle Cloud Always Free) pays after.

## Decision

**One environment, production.** What stands in for staging is the local rehearsal stack
(`make up-prodlike`: the same images and compose file, real keys, run before every merge) and
production itself while it's behind the access code. `ENV=production` everywhere; `staging` is
retired. A second cloud environment is added when traffic or risk justifies its cost, not before.

**One always-on host.** An EC2 `t4g.small` (2 vCPU, 2 GB, arm64, Ubuntu 24.04 LTS) in one AZ of
`us-east-1`, with an Elastic IP, running Docker Compose: Caddy (TLS from Let's Encrypt, routing by
hostname), the web tier (nginx: SPA and `/v1` proxy), the api, the worker, Postgres 16 with
pgvector and Redis. Postgres and Redis keep their data on a separate encrypted volume that
outlives the instance. Ubuntu, not Amazon Linux, so one host script runs unchanged on Oracle or a
VPS. The site can't sleep after launch, so the host is always on; before launch it can be
stopped (`make host-stop`).

**Portable by construction.** The containers, the compose file, Caddy, the host script, the
backup format, the images (GHCR, public) and DNS (Cloudflare, DNS only) know nothing about AWS.
AWS appears only in `infra/terraform/`, the AWS half of the host's deploy and backup scripts,
and CI's AWS steps. Nothing in the containers holds AWS credentials; the host reads SSM and
writes the backups; IMDSv2 has a hop limit of 1, so containers can't reach it.

**Deploys** run from CI over SSM Run Command (GitHub OIDC, no stored keys): pull the images by
git SHA, write the env file, migrate as a one-off (a failure keeps the old release serving), bring
the stack up, wait for `/readyz` and put the previous tag back if it doesn't come. A rollback is
`make deploy SHA=<sha>`, without migrations, safe because of one release of compatibility
(NFR-10.4). **Backups:** daily snapshots of the data volume (7 kept) and a nightly `pg_dump` to a
private, versioned S3 bucket (30 days). **Patching:** `unattended-upgrades`, reboots in a weekly
window.

**Costs.** About $18 a month, drawn from the credits: the instance about $12, its public IPv4
address $3.60, disks and snapshots about $2, the bucket cents. $200 lasts about 11 months, and
the Free plan closes the account at month 6, so by month 5 the app moves (ledger 51) or the
account is upgraded to the Paid plan, which keeps the leftover credit. Two Budgets watch it: $1 a
month of net cost, $25 of gross.

**The move** swaps only these: EC2 → an Ampere A1 instance (arm64), Elastic IP → a reserved
public IP, SSM parameters → a root-only env file, SSM deploys → SSH from CI, snapshots and S3
dumps → block-volume backups and object storage, Terraform's AWS provider → the OCI provider.
The fallback is a small ARM VPS.

## Alternatives considered

- **ADR-0012's topology, once:** about $80 a month, and most of its parts (ALB, NAT-free VPC,
  managed Redis and Postgres) are AWS-only, so none of it moves.
- **CloudFront in front of the host:** it would still need a trusted certificate on the host, a
  second hostname and headers set in two places. It can be added later without touching the app.
- **Two environments:** twice the cost, and nobody to protect at this traffic.
- **Amazon Linux:** a smaller gap to AWS, a bigger one to every other host.
- **ECR:** GHCR is free for a public repo's packages, needs no pull credentials and is where the
  images stay after the move. The registry concepts carry over.

## Consequences

One host is one failure domain: a bad deploy or a dead instance is an outage until the previous
tag or a restored volume is back. That's accepted at this audience, and the runbook covers both.
The move to a free host is due before month 5 of the AWS account, and its OCI Terraform is its
own story. Revisit when there is a second project on the domain (the `platform/` Terraform root
is built to be shared), real traffic, or an availability target.
