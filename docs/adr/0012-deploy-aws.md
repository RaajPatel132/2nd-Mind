# ADR-0012: Deploy on AWS ECS Fargate with Terraform and GitHub OIDC

- **Status:** proposed (decided in S5)
- **Date:** 2026-09-24

## Context

NFR-10.1–10.6: IaC for every resource, staging and production from the same code, eval-gated
releases, short-lived CI credentials, managed secrets.

## Decision (proposed)

ECS Fargate for `api` and `worker` (one image, two services), RDS Postgres with pgvector,
ElastiCache Redis, S3 + CloudFront for the SPA, Secrets Manager for secrets injected at start,
Terraform for all of it, and GitHub Actions deploying through OIDC-assumed roles.

## Alternatives considered

- **Kubernetes (EKS):** more control, much more to operate for two services.
- **A PaaS (Fly, Render):** quick, but weaker story for IaC, private networking and OIDC.

## Consequences

S1 prepares for it: config from env only, non-root images, one image for api and worker,
migrations as a separate one-shot task, health and readiness endpoints.
