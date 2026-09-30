# ADR-0012: Deploy on AWS ECS Fargate with Terraform and GitHub OIDC

- **Status:** accepted (S3.9); **topology superseded by ADR-0035** (S4.1). Its choices of region
  (`us-east-1`), CPU (`arm64`), Langfuse Cloud for tracing and GitHub OIDC for CI carry over.
  The CloudFront, ALB, Fargate, RDS and ElastiCache topology, the two environments and ECR do not.
- **Date:** 2026-09-24, accepted 2026-09-28, topology superseded 2026-09-30

## Context

NFR-10.1–10.6: IaC for every resource, staging and production from the same code, eval-gated
releases, short-lived CI credentials, managed secrets. NFR-2.3/2.4: encryption in transit and at
rest, limited trace retention. The app is already shaped for it (S1, S3.9): env-only config,
non-root read-only images, one image for api and worker, migrations as a one-off task, liveness
separate from readiness, SSE heartbeats, graceful shutdown. S4 should only have to type this.

## Decision

**Topology** (one VPC per environment, two AZs):

- **Edge:** CloudFront in front of two origins: an S3 bucket (the SPA, private, origin access
  control) and the ALB for `/v1/*`. The `/v1/*` behaviour has caching and compression off, all
  methods and the cookie forwarded, and the same security headers nginx sends locally (a response
  headers policy). One origin for the browser, so no CORS and `SameSite=Lax` cookies work.
- **ALB:** HTTPS only (ACM certificate), idle timeout 120 s (the API heartbeats every 15 s),
  target group health check on `/healthz`, deregistration delay 30 s.
- **ECS Fargate:** `api` (service, 1 task in staging, behind the ALB) and `worker` (service,
  1 task), both from the same image; `migrate` as a one-off task run by the deploy before the
  api's new task set starts. Stop timeout 45 s, above `SHUTDOWN_GRACE_S` (30 s). Tasks run in
  **public subnets with public IPs and no inbound rule except from the ALB**: no NAT gateway
  (about $32 a month each) for two tasks that only call out to model providers.
- **RDS Postgres 16** (16.5 or later: pgvector 0.8, `halfvec` and HNSW iterative scans, which
  ADR-0024 relies on), single-AZ `db.t4g.micro` in staging, encrypted, 7-day backups, in private
  subnets. The master user creates the `vector` extension and the owner role once (ADR-0033);
  migrations run as the owner with `CREATEROLE` only.
- **ElastiCache Redis 7** (`cache.t4g.micro`, one node, TLS and AUTH), private subnets. arq's
  queue, the kill-switch flag, spend counters and rate buckets (ADR-0032) live there.
- **Secrets Manager** for secrets (provider keys, `SESSION_SECRET`, `STAGING_ACCESS_CODE`, the
  database and Redis passwords, Langfuse keys), **SSM Parameter Store** for plain settings; ECS
  injects both at task start. `docs/deploy/config.md` says which variable comes from where.
- **ECR** for the image (immutable tags, the git SHA; scan on push). **GitHub OIDC** roles: one
  to push images and deploy staging from `main`, one for production behind a protected
  environment and the eval gate (S5). No stored AWS keys anywhere.

**Choices:**

- **Region `us-east-1`.** Every turn makes 3 to 5 sequential model calls to US-hosted provider
  APIs; putting compute next to them saves roughly 200 ms a call over `ap-south-1`, more than the
  browser's one extra round trip loses. Everything we use is there first and cheapest.
- **CPU `arm64` (Graviton), images `linux/arm64`.** About 20% cheaper on Fargate, and native on
  the Apple-silicon machines the images are built and tested on. CI builds on GitHub's arm64
  runners; no emulation.
- **Staging sizes:** api 0.5 vCPU / 1 GB, worker 0.25 vCPU / 0.5 GB, migrate 0.25 vCPU / 0.5 GB.
  The CPU and memory limits in `compose.prodlike.yaml` are the same, so the rehearsal runs in
  what staging will have.
- **One environment first:** S4 stands up staging (private, behind the staging access code).
  Production is added in S5 from the same modules with its own variables file.
- **Tracing (decision 4):** **Langfuse Cloud** for staging and production, metadata only
  (`TRACE_INCLUDE_CONTENT=false`), which saves running ClickHouse, Redis and MinIO on AWS;
  self-hosted Langfuse stays for local development. Retention (NFR-2.4) is the project's data
  retention setting, 30 days, set in the Langfuse project and recorded in the runbook.

**Cost** (staging, on-demand prices for `us-east-1`, per month):

| Item | At rest | At demo load (~2k turns) |
|---|---:|---:|
| ALB (1 LCU) | $18 | $20 |
| Fargate api + worker (arm64, always on) | $17 | $17 |
| RDS `db.t4g.micro` + 20 GB gp3 | $15 | $15 |
| ElastiCache `cache.t4g.micro` | $12 | $12 |
| Public IPv4 addresses (3) | $11 | $11 |
| CloudFront, S3, ECR, Secrets Manager, CloudWatch logs (7 days), Route 53 | $6 | $9 |
| **Infrastructure** | **~$79** | **~$84** |
| Model calls (economy routing, ~$0.006 a turn; capped by ADR-0032) | $0 | ~$12 |

The levers if that's too much, in order: scale both services to zero outside demo hours
(scheduled scaling), drop ElastiCache for Redis as a Fargate sidecar (loses durability of the
queue), and share one ALB between staging and production.

**Terraform layout** (`infra/terraform/`, Terraform ≥ 1.10):

```
modules/network   VPC, subnets, security groups
modules/data      RDS, ElastiCache, their secrets
modules/app       ECR, ECS cluster, services, the migrate task, IAM task roles, log groups
modules/edge      S3 bucket, CloudFront, ACM, Route 53 records, response headers policy
modules/ci        GitHub OIDC provider and the deploy roles
envs/staging      main.tf wiring the modules; staging.tfvars
envs/production   the same, added in S5
```

State in one S3 bucket per account (`secondmind-tfstate-<account>`, versioned, encrypted) with
S3 native locking (`use_lockfile = true`), a key per environment. The bucket is the one resource
made by hand, once, and the runbook says how.

## Alternatives considered

- **Kubernetes (EKS):** more control, much more to operate for two services, and $73 a month for
  the control plane alone.
- **A PaaS (Fly, Render):** quick, but weaker IaC, private networking and OIDC stories.
- **Private subnets with a NAT gateway:** the textbook layout, at $32+ a month per AZ for nothing
  but outbound calls to model APIs.
- **`ap-south-1`:** nearer the author, further from the providers every turn waits on.

## Consequences

The app has no AWS-specific code: the same image runs locally, in the rehearsal stack and on
Fargate. S4 writes the modules above and deploys the rehearsal stack as private staging on its
first day. Revisit the region if a provider opens an endpoint nearer our users, and the sizes
after the load test (NFR-10.8, S8).
