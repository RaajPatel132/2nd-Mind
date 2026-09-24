# ADR-0002: One Postgres 16 store (with pgvector) for relational, vector and lexical data

- **Status:** accepted
- **Date:** 2026-09-24

## Context

Memory items are relational (people, links, layers), need dense search (embeddings) and
lexical search (BM25-style), and must be isolated per workspace (NFR-2.1) with transactional
writes (NFR-6.1) and one backup story.

## Decision

A single Postgres 16 server with the `pgvector` extension holds everything: relational
tables, `vector` columns, and Postgres full-text search. Local and CI use
`pgvector/pgvector:pg16`. The exact BM25 flavour is decided in S3.

## Alternatives considered

- **Postgres + a dedicated vector DB (Pinecone, Qdrant, Chroma):** better ANN at scale, but
  isolation, deletes, backups and transactions would span two systems, and every write
  would become a distributed consistency problem.
- **Elasticsearch/OpenSearch for lexical + vector:** strong search, heavy to operate, and
  still a second store for the relational core.

## Consequences

One place to enforce row-level security, one transaction per turn, one backup. We accept
pgvector's ANN limits; at Phase 1 volumes (thousands of items per workspace) they don't
bite. Revisit if p95 retrieval latency or index build time becomes the bottleneck.
