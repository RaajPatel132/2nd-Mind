"""Vector storage bench (S3.3, ADR-0024): ``make bench-vectors``.

One table shaped like ``memory_keys`` (workspace id, 1536-d embedding) under the same RLS policy,
holding three target workspaces of 1k, 10k and 50k items (four keys each) and 20 other
workspaces. The soft channel's dense query (top 10 of one workspace by cosine distance) is timed
as a non-owner role, so RLS applies, for:

* an exact scan filtered by workspace (a btree on workspace_id, no vector index);
* HNSW with pgvector's iterative index scans for the workspace filter;
* ``halfvec`` HNSW at 1536 dimensions, and at 768 (the first half of the vector; the OpenAI
  ``text-embedding-3`` models are trained so a prefix is still a good embedding).

It reports p50/p95 latency, recall@10 against the exact scan (by distance, so a tie returned in
another order counts), index build time and size. With ``OPENAI_API_KEY`` set the vectors are
real embeddings of templated synthetic sentences (the cost is printed; the run refuses past
$0.50); otherwise they're clustered random vectors, and the report says which. Everything
runs in a throwaway pgvector container.
"""

import argparse
import asyncio
import io
import math
import os
import random
import statistics
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
from testcontainers.postgres import PostgresContainer

from secondmind.evals.spend import Budgets, SpendBook

DIM = 1536
KEYS_PER_ITEM = 4
TARGETS = (1_000, 10_000, 50_000)
OTHERS, OTHER_ITEMS = 20, 500
QUERIES = 40
PRICE_PER_MTOK = 0.02  # text-embedding-3-small, USD per 1M tokens
BUDGET_USD = 0.50

PEOPLE = ["Nisha", "Kabir", "Rohan", "Meenu", "Arjun", "Leela", "Tara", "Dev", "Isha", "Sam"]
ACTS = [
    "ran", "cooked", "read about", "watched", "bought", "planned", "fixed", "visited", "learned",
    "talked about", "saved an article on", "booked", "cancelled", "tried",
]  # fmt: skip
THINGS = [
    "a 5 km loop", "pasta with basil", "sleep hygiene", "a crime series", "running shoes",
    "a trip to Goa", "the kitchen tap", "a new cafe", "Rust ownership", "index funds",
    "a pottery class", "the dentist", "birdwatching gear", "a jazz night", "a slow weekend",
    "guitar chords", "a birthday gift", "a passport renewal", "a board game", "a ramen bar",
]  # fmt: skip
WHEN = ["on Monday", "last weekend", "in September", "this morning", "yesterday", "next month"]
KINDS = ["fact", "note", "plan", "episode"]


def sentence(rng: random.Random) -> str:
    return (
        f"{rng.choice(KINDS).title()}: {rng.choice(PEOPLE)} {rng.choice(ACTS)} "
        f"{rng.choice(THINGS)} {rng.choice(WHEN)}."
    )


def clustered(rng: random.Random, centres: Sequence[Sequence[float]]) -> list[float]:
    c = rng.choice(centres)
    v = [x + rng.gauss(0, 0.35) for x in c]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def literal(v: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


class Vectors:
    """Real embeddings (OpenAI) or clustered random vectors, in batches."""

    def __init__(self, *, real: bool, seed: int = 7) -> None:
        self.real = real
        self.rng = random.Random(seed)
        self.tokens = 0
        self.centres = [[self.rng.gauss(0, 1) for _ in range(DIM)] for _ in range(64)]
        if real:
            from openai import AsyncOpenAI  # noqa: PLC0415

            self.client = AsyncOpenAI(max_retries=10)  # a long run outlives a network blip

    async def batch(self, n: int) -> list[list[float]]:
        if not self.real:
            return [clustered(self.rng, self.centres) for _ in range(n)]
        texts = [sentence(self.rng) for _ in range(n)]
        out = await self.client.embeddings.create(model="text-embedding-3-small", input=texts)
        self.tokens += out.usage.total_tokens
        return [d.embedding for d in out.data]

    @property
    def cost(self) -> float:
        return self.tokens / 1_000_000 * PRICE_PER_MTOK


def chunks(total: int, size: int) -> Iterator[int]:
    while total > 0:
        yield min(size, total)
        total -= size


async def load(conn: asyncpg.Connection, vectors: Vectors) -> dict[str, uuid.UUID]:
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.execute(
        f"""CREATE TABLE keys (id bigserial PRIMARY KEY, workspace_id uuid NOT NULL,
            embedding vector({DIM}) NOT NULL, half halfvec({DIM}), half768 halfvec(768))"""
    )
    await conn.execute("CREATE INDEX keys_ws ON keys (workspace_id)")
    await conn.execute("ALTER TABLE keys ENABLE ROW LEVEL SECURITY")
    await conn.execute(
        """CREATE POLICY ws ON keys USING
           (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"""
    )
    await conn.execute("CREATE ROLE bench_app NOLOGIN")
    await conn.execute("GRANT SELECT ON keys TO bench_app")
    spaces = {f"target_{n}": uuid.uuid4() for n in TARGETS}
    plan = [(spaces[f"target_{n}"], n * KEYS_PER_ITEM) for n in TARGETS]
    plan += [(uuid.uuid4(), OTHER_ITEMS * KEYS_PER_ITEM) for _ in range(OTHERS)]
    total = sum(n for _, n in plan)
    done = 0
    for ws, rows in plan:
        for n in chunks(rows, 1000):
            vs = await vectors.batch(n)
            # CSV text: asyncpg's binary COPY has no encoder for pgvector's types.
            csv = "".join(f'{ws},"{literal(v)}"\n' for v in vs).encode()
            await conn.copy_to_table(
                "keys", source=io.BytesIO(csv), columns=["workspace_id", "embedding"], format="csv"
            )
            done += n
            if vectors.real and vectors.cost > BUDGET_USD:
                raise SystemExit(f"stopping: embeddings would pass ${BUDGET_USD:.2f}")
            sys.stderr.write(f"\r  loaded {done:,}/{total:,} keys")
    sys.stderr.write("\n")
    await conn.execute(
        "UPDATE keys SET half = embedding::halfvec, half768 = subvector(embedding, 1, 768)::halfvec"
    )
    await conn.execute("ANALYZE keys")
    return spaces


async def run_queries(
    conn: asyncpg.Connection,
    ws: uuid.UUID,
    queries: Sequence[str],
    column: str,
    cast: str,
    *,
    full: Sequence[str],
) -> tuple[list[float], list[list[float]], str]:
    """Latencies, the true (full ``vector(1536)``) distances of each query's top 10, and the plan
    Postgres chose (``hnsw`` or ``exact``: under RLS the planner may keep the workspace btree and
    sort instead of using the vector index)."""
    times: list[float] = []
    results: list[list[float]] = []
    async with conn.transaction():
        await conn.execute("SET LOCAL ROLE bench_app")
        await conn.execute(f"SELECT set_config('app.workspace_id', '{ws}', true)")
        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        await conn.execute("SET LOCAL hnsw.ef_search = 40")
        sql = (
            f"SELECT embedding <=> $2::vector AS d FROM keys "
            f"ORDER BY {column} <=> $1::{cast} LIMIT 10"
        )
        explained = await conn.fetchval(f"EXPLAIN (FORMAT JSON) {sql}", queries[0], full[0])
        plan = "hnsw" if f"idx_{column}" in str(explained) else "exact"
        stmt = await conn.prepare(sql)
        for q, f in list(zip(queries, full, strict=True))[:3]:  # warm-up
            await stmt.fetch(q, f)
        for q, f in zip(queries, full, strict=True):
            t0 = time.perf_counter()
            rows = await stmt.fetch(q, f)
            times.append((time.perf_counter() - t0) * 1000)
            results.append([float(r["d"]) for r in rows])
    return times, results, plan


def recall_at_10(got: Sequence[Sequence[float]], exact: Sequence[Sequence[float]]) -> float:
    """Share of results at least as close as the exact 10th neighbour. By distance, not by id:
    templated sentences repeat, and a tie returned in another order is not a miss."""
    per_query = []
    for g, e in zip(got, exact, strict=True):
        kth = e[-1] if e else 0.0
        per_query.append(sum(d <= kth + 1e-6 for d in g) / max(1, len(e)))
    return statistics.mean(per_query)


def pct(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


async def main(vectors: "Vectors") -> int:
    real = vectors.real
    mode = "real OpenAI text-embedding-3-small embeddings" if real else "clustered random vectors"
    sys.stderr.write(f"bench-vectors: {mode}\n")
    # Parallel HNSW builds use shared memory up to maintenance_work_mem; Docker's default
    # /dev/shm is 64 MB.
    container = PostgresContainer("pgvector/pgvector:pg16", driver=None).with_kwargs(shm_size="2g")
    with container as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        conn = await asyncpg.connect(url)
        await conn.execute("SET maintenance_work_mem = '1GB'")
        spaces = await load(conn, vectors)
        queries_v = [literal(v) for v in await vectors.batch(QUERIES)]
        halves = [literal([float(x) for x in q.strip("[]").split(",")][:768]) for q in queries_v]
        rows: list[tuple[str, int, str, float, float, float, float, float]] = []
        exact: dict[int, list[list[float]]] = {}
        for n in TARGETS:
            t, r, plan = await run_queries(
                conn, spaces[f"target_{n}"], queries_v, "embedding", "vector", full=queries_v
            )
            exact[n] = r
            rows.append(("exact", n, plan, pct(t, 0.5), pct(t, 0.95), 1.0, 0.0, 0.0))
        variants = [
            ("hnsw vector(1536)", "embedding", "vector", "vector_cosine_ops", queries_v),
            ("hnsw halfvec(1536)", "half", "halfvec", "halfvec_cosine_ops", queries_v),
            ("hnsw halfvec(768)", "half768", "halfvec", "halfvec_cosine_ops", halves),
        ]
        for name, column, cast, ops, qs in variants:
            t0 = time.perf_counter()
            await conn.execute(f"CREATE INDEX idx_{column} ON keys USING hnsw ({column} {ops})")
            build = time.perf_counter() - t0
            size = await conn.fetchval(f"SELECT pg_relation_size('idx_{column}')") / 1024**2
            for n in TARGETS:
                t, r, plan = await run_queries(
                    conn, spaces[f"target_{n}"], qs, column, cast, full=queries_v
                )
                recall = recall_at_10(r, exact[n])
                rows.append((name, n, plan, pct(t, 0.5), pct(t, 0.95), recall, build, size))
            await conn.execute(f"DROP INDEX idx_{column}")
        table_mb = await conn.fetchval("SELECT pg_total_relation_size('keys')") / 1024**2
        total = await conn.fetchval("SELECT count(*) FROM keys")
        await conn.close()
    print(f"\nbench-vectors · {mode} · {total:,} keys in {len(TARGETS) + OTHERS} workspaces")
    print(f"(table with all columns {table_mb:,.0f} MB; embeddings cost ${vectors.cost:.4f})\n")
    print("| variant | items | plan | p50 ms | p95 ms | recall@10 | build s | index MB |")
    print("|---|---:|---|---:|---:|---:|---:|---:|")
    for name, n, plan, p50, p95, recall, build, size in rows:
        b = f"{build:.1f}" if build else "—"
        s = f"{size:,.0f}" if size else "—"
        print(f"| {name} | {n:,} | {plan} | {p50:.2f} | {p95:.2f} | {recall:.3f} | {b} | {s} |")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="bench-vectors")
    parser.add_argument("--random", action="store_true", help="clustered random vectors only")
    args = parser.parse_args()
    use_real = bool(os.environ.get("OPENAI_API_KEY")) and not args.random
    # colima and Docker Desktop: inside the VM the daemon socket is at the standard path (Ryuk).
    os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")
    budgets = Budgets.from_env()
    book = SpendBook.load()
    if use_real:
        # Real embeddings go on the live spend total, within the sprint budget (R.1).
        book.effective_budget(
            Budgets(run=Decimal(str(BUDGET_USD)), total=budgets.total, batch=budgets.batch)
        )
    bench_vectors = Vectors(real=use_real)
    try:
        code = asyncio.run(main(bench_vectors))
    finally:
        if use_real:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            book.record(
                run_id=f"bench-vectors-{stamp}",
                suite="bench-vectors",
                spent_by_provider={"openai": Decimal(str(round(bench_vectors.cost, 6)))},
                budget=Decimal(str(BUDGET_USD)),
                batch=budgets.batch,
            )
            print(book.summary(budgets.total))
    raise SystemExit(code)
