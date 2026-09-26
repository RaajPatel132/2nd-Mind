"""The retrieval tools over Postgres (S3.5, S3.6). Each tool is one SQL statement (CTEs are fine),
run in a **read-only** transaction bound to the turn's workspace (RLS), with a statement
timeout. Every statement carries the mandatory filters: ``status = 'active'``, never
``secret``, and ``sensitive`` only when the question is about it.

Hybrid search (ADR-0026): the lexical half is Postgres full text (``ts_rank_cd`` over the
generated ``tsv``, an OR query of the question's words); the dense half is cosine distance on
the key's embedding. Each half ranks **items** by their best key, and the two ranks are fused
by RRF inside the channel. The key kind that gave an item its best rank is reported.
"""

import re
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from secondmind.core import LIVE_STATES, KeyKind, TimeClock, TimePrecision, WorkspaceScope
from secondmind.memory import expand_rrule, occurrence_length
from secondmind.memory.adapters import Database
from secondmind.retrieval.tools import (
    INVERSE,
    SYMMETRIC,
    Access,
    AggregateOp,
    AggregateResult,
    ConversationHit,
    EntityResult,
    Filters,
    Group,
    GroupBy,
    HistoryResult,
    HistoryRow,
    Hit,
    LookupResult,
    Occurrence,
    PathHop,
    Query,
    SetOp,
    TimelineResult,
    WindowFilter,
)

_LIVE = sorted(LIVE_STATES)
_POOL = 200  # keys each half of a hybrid search considers
_WORD = re.compile(r"[a-z0-9]+")


def tsquery_text(query: str) -> str | None:
    """The question's words as an OR query (``run | september | 2026``). Only letters and
    digits reach ``to_tsquery``, so nothing in the text can change the query's syntax."""
    words = list(dict.fromkeys(_WORD.findall(query.lower())))
    return " | ".join(words) if words else None


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(v):.7g}" for v in vector) + "]"


def _variants(value: str) -> list[str]:
    """Forms an attribute value is matched in: "running" also matches "run" and "runs"."""
    v = value.strip().lower()
    out = {v, v + "s", v.removesuffix("s")}
    if v.endswith("ning") and len(v) > 5:
        out.add(v[:-4])
    if v.endswith("ing") and len(v) > 4:
        out.add(v[:-3])
    return sorted(out)


class _Where:
    """Builds the WHERE clause of one statement, with its bind parameters."""

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self._n = 0

    def bind(self, value: Any) -> str:
        self._n += 1
        name = f"p{self._n}"
        self.params[name] = value
        return f":{name}"

    def mandatory(self, access: Access, a: str = "i") -> list[str]:
        parts = [f"{a}.status = 'active'", f"{a}.sensitivity <> 'secret'"]
        if not access.sensitive:
            parts.append(f"{a}.sensitivity <> 'sensitive'")
        return parts

    def filters(self, f: Filters, access: Access, a: str = "i", *, window: bool = True) -> str:
        parts = self.mandatory(access, a)
        if f.kinds:
            parts.append(f"{a}.kind = ANY({self.bind([k.value for k in f.kinds])})")
        if f.subtypes:
            parts.append(f"{a}.subtype = ANY({self.bind(list(f.subtypes))})")
        if f.states:
            parts.append(f"{a}.state = ANY({self.bind(list(f.states))})")
        if f.category:
            slug = self.bind(f.category)
            parts.append(
                f"{a}.category_id IN (SELECT c.id FROM categories c WHERE c.slug = {slug} "
                f"OR c.slug LIKE {slug} || '/%')"
            )
        if f.entity_ids:
            ids = self.bind(list(f.entity_ids))
            roles = ""
            if f.roles:
                roles = f" AND me.role = ANY({self.bind([r.value for r in f.roles])})"
            linked = f"{a}.id IN (SELECT me.item_id FROM memory_entities me WHERE me.entity_id = ANY({ids}){roles})"  # noqa: E501
            subject_ok = not f.roles or any(r.value == "about" for r in f.roles)
            parts.append(
                f"({linked} OR {a}.subject_entity_id = ANY({ids}))" if subject_ok else linked
            )
        if f.predicate:
            parts.append(f"{a}.predicate = {self.bind(f.predicate)}")
        if f.attribute:
            key, value = f.attribute
            parts.append(
                f"lower(trim({a}.attributes ->> {self.bind(key)})) = ANY({self.bind(_variants(value))})"  # noqa: E501
            )
        if f.current_only:
            parts.append(f"{a}.valid_to IS NULL")
        if window and f.window is not None:
            clause = self.window(f.window, a)
            if clause:
                parts.append(clause)
        return " AND ".join(parts)

    def window(self, w: WindowFilter, a: str = "i") -> str:
        start = self.bind(w.start) if w.start is not None else None
        end = self.bind(w.end) if w.end is not None else None
        match w.clock:
            case TimeClock.OCCURRED:
                bits = [f"{a}.occurred_start IS NOT NULL"]
                if end:
                    bits.append(f"{a}.occurred_start < {end}")
                if start:
                    bits.append(
                        f"coalesce({a}.occurred_end, {a}.occurred_start + interval '1 second') "
                        f"> {start}"
                    )
                return "(" + " AND ".join(bits) + ")"
            case TimeClock.DUE:
                return self.point(f"{a}.due_at", start, end)
            case TimeClock.MENTIONED:
                return self.point(f"{a}.mentioned_at", start, end)
            case TimeClock.VALID:
                bits = []
                if end:
                    bits.append(f"({a}.valid_from IS NULL OR {a}.valid_from < {end})")
                if start:
                    bits.append(f"({a}.valid_to IS NULL OR {a}.valid_to > {start})")
                return "(" + " AND ".join(bits) + ")" if bits else ""
        return (
            f"{a}.id IN (SELECT t.item_id FROM triggers t WHERE t.state = 'pending' "
            f"AND {self.point('t.fires_at', start, end)})"
        )

    @staticmethod
    def point(column: str, start: str | None, end: str | None) -> str:
        """``column`` within ``[start, end)``; ``start`` and ``end`` are bound names or None."""
        bits = [f"{column} IS NOT NULL"]
        if start:
            bits.append(f"{column} >= {start}")
        if end:
            bits.append(f"{column} < {end}")
        return "(" + " AND ".join(bits) + ")"


_ORDER_LIVE = (
    "CASE WHEN {a}.valid_to IS NULL AND {a}.state = ANY(:live) THEN 0 ELSE 1 END, "
    "coalesce({a}.occurred_start, {a}.due_at, {a}.valid_from, {a}.mentioned_at) DESC, {a}.id"
)


class SqlRecallStore:
    """``RecallStore`` over Postgres for one workspace."""

    def __init__(
        self,
        db: Database,
        scope: WorkspaceScope,
        *,
        timeout_ms: int | None = None,
        rrf_k: int = 60,
    ) -> None:
        self._db = db
        self._scope = scope
        self._timeout_ms = timeout_ms
        self._rrf_k = rrf_k

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[AsyncSession]:
        async with self._db.workspace(
            self._scope, read_only=True, timeout_ms=self._timeout_ms
        ) as session:
            yield session

    # ------------------------------------------------------------------ lookup

    async def lookup(
        self,
        filters: Filters,
        access: Access,
        *,
        set_op: SetOp | None = None,
        limit: int = 50,
    ) -> LookupResult:
        w = _Where()
        where = w.filters(filters, access)
        if set_op is not None:
            where += " AND " + _set_op(set_op, w)
        sql = f"""
            SELECT i.id, count(*) OVER () AS total
            FROM memory_items i
            WHERE {where}
            ORDER BY {_ORDER_LIVE.format(a="i")}
            LIMIT :limit
        """
        async with self._tx() as s:
            rows = (await s.execute(text(sql), {**w.params, "live": _LIVE, "limit": limit})).all()
        return LookupResult(
            hits=[Hit(item_id=r[0], rank=n + 1) for n, r in enumerate(rows)],
            total=int(rows[0][1]) if rows else 0,
        )

    # ------------------------------------------------------------------ aggregate

    async def aggregate(
        self,
        filters: Filters,
        access: Access,
        *,
        op: AggregateOp,
        field: str | None = None,
        group_by: GroupBy | None = None,
        timezone: str = "UTC",
    ) -> AggregateResult:
        """Exact numbers computed by SQL (window aggregates), with the ids they counted."""
        if op != "count" and field is None:
            raise ValueError(f"{op} needs a field")
        w = _Where()
        where = w.filters(filters, access)
        value = "NULL::numeric"
        if field is not None:
            if field in ("value", "value.number"):
                raw = "i.value ->> 'number'"
            else:
                raw = f"i.attributes ->> {w.bind(field.removeprefix('attributes.'))}"
            value = f"CASE WHEN ({raw}) ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN ({raw})::numeric END"
        when = "coalesce(i.occurred_start, i.due_at, i.mentioned_at)"
        tz = w.bind(timezone)
        group = {
            None: "''",
            "day": f"to_char(date_trunc('day', {when} AT TIME ZONE {tz}), 'YYYY-MM-DD')",
            "week": f"to_char(date_trunc('week', {when} AT TIME ZONE {tz}), 'IYYY-\"W\"IW')",
            "month": f"to_char(date_trunc('month', {when} AT TIME ZONE {tz}), 'YYYY-MM')",
            "subtype": "coalesce(i.subtype, '(none)')",
        }[group_by]
        fn = {
            "count": "count(*)",
            "sum": "sum(v)",
            "min": "min(v)",
            "max": "max(v)",
            "average": "avg(v)",
        }[op]
        counted = "true" if op == "count" else "v IS NOT NULL"
        sql = f"""
            WITH rows AS (
                SELECT i.id, {value} AS v, {group} AS g, {when} AS at
                FROM memory_items i
                WHERE {where}
            ),
            counted AS (SELECT * FROM rows WHERE {counted})
            SELECT id, g,
                   {fn} OVER () AS total,
                   {fn} OVER (PARTITION BY g) AS group_value,
                   count(*) OVER (PARTITION BY g) AS group_count
            FROM counted
            ORDER BY at, id
        """
        async with self._tx() as s:
            rows = (await s.execute(text(sql), w.params)).all()
        groups: dict[str, Group] = {}
        for r in rows:
            if group_by is not None and r[1] not in groups and r[3] is not None:
                groups[r[1]] = Group(key=str(r[1]), value=float(r[3]), count=int(r[4]))
        total: float | None
        if op == "count":
            total = float(rows[0][2]) if rows else 0.0
        else:
            total = float(rows[0][2]) if rows and rows[0][2] is not None else None
        return AggregateResult(
            op=op, value=total, item_ids=[r[0] for r in rows], groups=list(groups.values())
        )

    # ------------------------------------------------------------------ hybrid search

    async def search(
        self, query: Query, filters: Filters, access: Access, *, limit: int
    ) -> list[Hit]:
        w = _Where()
        where = w.filters(filters, access)
        tsq = tsquery_text(query.text)
        lexical = (
            "SELECT NULL::uuid AS item_id, NULL::text AS key_kind, NULL::real AS score WHERE false"
        )
        if tsq is not None:
            q = w.bind(tsq)
            lexical = f"""
                SELECT k.item_id, k.key_kind,
                       ts_rank_cd(k.tsv, to_tsquery('english', {q}), 32) AS score
                FROM memory_keys k
                WHERE k.tsv @@ to_tsquery('english', {q})
                  AND k.item_id IN (SELECT id FROM eligible)
                ORDER BY score DESC, k.id
                LIMIT {_POOL}
            """
        dense = (
            "SELECT NULL::uuid AS item_id, NULL::text AS key_kind, NULL::float AS score WHERE false"
        )
        if query.vector is not None:
            vec = w.bind(vector_literal(query.vector))
            model = w.bind(query.model)
            dense = f"""
                SELECT k.item_id, k.key_kind,
                       1 - (k.embedding <=> CAST({vec} AS vector)) AS score
                FROM memory_keys k
                WHERE k.embedding IS NOT NULL AND k.embedding_model = {model}
                  AND k.item_id IN (SELECT id FROM eligible)
                ORDER BY k.embedding <=> CAST({vec} AS vector), k.id
                LIMIT {_POOL}
            """
        sql = f"""
            WITH eligible AS (SELECT i.id FROM memory_items i WHERE {where}),
            lex_keys AS ({lexical}),
            dense_keys AS ({dense}),
            lex AS (
                SELECT item_id, key_kind, score,
                       row_number() OVER (ORDER BY score DESC, item_id) AS rnk
                FROM (SELECT DISTINCT ON (item_id) item_id, key_kind, score
                      FROM lex_keys ORDER BY item_id, score DESC) best
            ),
            dns AS (
                SELECT item_id, key_kind, score,
                       row_number() OVER (ORDER BY score DESC, item_id) AS rnk
                FROM (SELECT DISTINCT ON (item_id) item_id, key_kind, score
                      FROM dense_keys ORDER BY item_id, score DESC) best
            )
            SELECT coalesce(l.item_id, d.item_id) AS item_id,
                   l.score AS lexical, d.score AS dense,
                   CASE WHEN d.rnk IS NULL OR (l.rnk IS NOT NULL AND l.rnk <= d.rnk)
                        THEN l.key_kind ELSE d.key_kind END AS key_kind,
                   coalesce(1.0 / (:rrf_k + l.rnk), 0) + coalesce(1.0 / (:rrf_k + d.rnk), 0)
                       AS rrf
            FROM lex l FULL OUTER JOIN dns d ON d.item_id = l.item_id
            ORDER BY rrf DESC, item_id
            LIMIT :limit
        """
        async with self._tx() as s:
            rows = (
                await s.execute(text(sql), {**w.params, "rrf_k": self._rrf_k, "limit": limit})
            ).all()
        return [
            Hit(
                item_id=r[0],
                rank=n + 1,
                lexical=float(r[1]) if r[1] is not None else None,
                dense=float(r[2]) if r[2] is not None else None,
                matched_key=KeyKind(r[3]) if r[3] else None,
            )
            for n, r in enumerate(rows)
        ]

    # ------------------------------------------------------------------ entity (+ 2 hops)

    async def entity(
        self,
        entity_ids: Sequence[uuid.UUID],
        access: Access,
        *,
        path: Sequence[PathHop] = (),
        limit: int = 50,
    ) -> EntityResult:
        if len(path) > 2:
            raise ValueError("relation paths are at most 2 hops")
        w = _Where()
        hops = [*path, PathHop(""), PathHop("")][:2]
        params: dict[str, Any] = {
            "base": list(entity_ids),
            "hops": len(path),
            "limit": limit,
            "live": _LIVE,
        }
        for n, hop in enumerate(hops, start=1):
            forward = [hop.relation] if hop.relation else []
            backward = []
            if hop.relation in SYMMETRIC:
                backward.append(hop.relation)
            if hop.relation in INVERSE:
                backward.append(INVERSE[hop.relation])
            params[f"fwd{n}"] = forward
            params[f"bwd{n}"] = backward
        mandatory = " AND ".join(w.mandatory(access))
        sql = f"""
            WITH RECURSIVE walk(entity_id, depth, path) AS (
                SELECT e.id, 0, ARRAY[e.name]::text[]
                FROM entities e WHERE e.id = ANY(:base) AND e.status = 'active'
                UNION ALL
                SELECT nxt.id, w.depth + 1, w.path || r.relation || nxt.name
                FROM walk w
                JOIN entity_relations r ON r.valid_to IS NULL AND (
                    (r.dst_entity_id = w.entity_id AND r.relation = ANY(
                        CASE WHEN w.depth = 0 THEN CAST(:fwd1 AS text[]) ELSE CAST(:fwd2 AS text[]) END))
                    OR (r.src_entity_id = w.entity_id AND r.relation = ANY(
                        CASE WHEN w.depth = 0 THEN CAST(:bwd1 AS text[]) ELSE CAST(:bwd2 AS text[]) END)))
                JOIN entities nxt ON nxt.status = 'active' AND nxt.id = CASE
                    WHEN r.dst_entity_id = w.entity_id THEN r.src_entity_id
                    ELSE r.dst_entity_id END
                WHERE w.depth < :hops
            ),
            reached AS (SELECT DISTINCT ON (entity_id) entity_id, path FROM walk WHERE depth = :hops),
            linked AS (
                SELECT i.id,
                       row_number() OVER (ORDER BY {_ORDER_LIVE.format(a="i")}) AS rnk
                FROM memory_items i
                WHERE {mandatory}
                  AND (i.subject_entity_id IN (SELECT entity_id FROM reached)
                       OR i.id IN (SELECT me.item_id FROM memory_entities me
                                   WHERE me.entity_id IN (SELECT entity_id FROM reached)))
            )
            SELECT 'entity' AS what, entity_id, path, NULL::uuid AS item_id, NULL::bigint AS rnk
            FROM reached
            UNION ALL
            SELECT 'item', NULL, NULL, id, rnk FROM linked WHERE rnk <= :limit
            ORDER BY what, rnk
        """  # noqa: E501 - clauses built from constants; values are bound
        async with self._tx() as s:
            rows = (await s.execute(text(sql), {**w.params, **params})).all()
        return EntityResult(
            entity_ids=[r[1] for r in rows if r[0] == "entity"],
            paths=[list(r[2]) for r in rows if r[0] == "entity"],
            hits=[Hit(item_id=r[3], rank=int(r[4])) for r in rows if r[0] == "item"],
        )

    # ------------------------------------------------------------------ timeline

    async def timeline(
        self,
        window: WindowFilter,
        filters: Filters,
        access: Access,
        *,
        now: datetime,
        timezone: str,
        limit: int = 100,
    ) -> TimelineResult:
        """Items whose time falls in the window: dated items and routine occurrences (for the
        occurred clock also tasks due and pending reminders), each marked past or upcoming."""
        w = _Where()
        where = w.filters(filters.without(window=None), access)
        start = w.bind(window.start) if window.start is not None else None
        end = w.bind(window.end) if window.end is not None else None
        clock = window.clock
        occurred = ["i.rrule IS NULL", "i.occurred_start IS NOT NULL"]
        if end:
            occurred.append(f"i.occurred_start < {end}")
        if start:
            occurred.append(
                f"coalesce(i.occurred_end, i.occurred_start + interval '1 second') > {start}"
            )
        routine = ["i.rrule IS NOT NULL", "i.state = 'scheduled'", "i.occurred_start IS NOT NULL"]
        if end:
            routine.append(f"i.occurred_start < {end}")
        valid = [
            "(i.valid_from IS NOT NULL OR i.valid_to IS NOT NULL "
            "OR i.kind IN ('fact', 'preference'))"
        ]
        if end:
            valid.append(f"(i.valid_from IS NULL OR i.valid_from < {end})")
        if start:
            valid.append(f"(i.valid_to IS NULL OR i.valid_to > {start})")
        branches: list[str] = []
        if clock is TimeClock.OCCURRED:
            branches += [
                f"""SELECT i.id, 'occurred' AS via, i.occurred_start AS at, i.occurred_end AS
                    until, NULL AS rrule, i.time_precision AS precision FROM memory_items i
                    WHERE {where} AND {" AND ".join(occurred)}""",
                f"""SELECT i.id, 'routine', i.occurred_start, NULL, i.rrule, i.time_precision
                    FROM memory_items i WHERE {where} AND {" AND ".join(routine)}""",
            ]
        if clock in (TimeClock.OCCURRED, TimeClock.DUE):
            branches.append(
                f"""SELECT i.id, 'due', i.due_at, NULL, NULL, i.time_precision
                    FROM memory_items i WHERE {where} AND {w.point("i.due_at", start, end)}"""
            )
        if clock in (TimeClock.OCCURRED, TimeClock.TRIGGER):
            branches.append(
                f"""SELECT i.id, 'trigger', t.fires_at, NULL, NULL, 'datetime'
                    FROM triggers t JOIN memory_items i ON i.id = t.item_id
                    WHERE {where} AND t.state = 'pending' AND t.on = 'time'
                      AND {w.point("t.fires_at", start, end)}"""
            )
        if clock is TimeClock.VALID:
            branches.append(
                f"""SELECT i.id, 'valid', coalesce(i.valid_from, i.mentioned_at), i.valid_to,
                    NULL, i.time_precision FROM memory_items i
                    WHERE {where} AND {" AND ".join(valid)}"""
            )
        if clock is TimeClock.MENTIONED:
            branches.append(
                f"""SELECT i.id, 'mentioned', i.mentioned_at, NULL, NULL, 'datetime'
                    FROM memory_items i
                    WHERE {where} AND {w.point("i.mentioned_at", start, end)}"""
            )
        sql = " UNION ALL ".join(branches) + " ORDER BY 3, 1"
        async with self._tx() as s:
            rows = (await s.execute(text(sql), w.params)).all()
        out: list[Occurrence] = []
        for item_id, via, at, until, rrule, precision in rows:
            if via == "routine":
                length = occurrence_length(TimePrecision(precision) if precision else None)
                for begins, ends in expand_rrule(
                    rrule,
                    at,
                    length,
                    start=window.start,
                    end=window.end,
                    timezone=timezone,
                    limit=limit,
                ):
                    out.append(Occurrence(item_id, begins, ends, "routine", begins >= now))
                continue
            out.append(Occurrence(item_id, at, until, via, at >= now))
        out.sort(key=lambda o: (o.start, str(o.item_id)))
        return TimelineResult(occurrences=out[:limit])

    # ------------------------------------------------------------------ history

    async def history(
        self,
        access: Access,
        *,
        subject_entity_id: uuid.UUID | None = None,
        predicate: str | None = None,
        item_ids: Sequence[uuid.UUID] = (),
    ) -> HistoryResult:
        """The supersede chain (oldest to newest, with validity and ``change`` keys) for a
        subject + predicate or for items, and the items linked to it by ``because``. Rows
        corrected as mistakes are archived, so they never appear."""
        w = _Where()
        mandatory = " AND ".join(w.mandatory(access))
        other = " AND ".join(w.mandatory(access, "o"))
        seeds = []
        if subject_entity_id is not None and predicate is not None:
            seeds.append(
                f"(i.subject_entity_id = {w.bind(subject_entity_id)} "
                f"AND i.predicate = {w.bind(predicate)})"
            )
        if item_ids:
            seeds.append(f"i.id = ANY({w.bind(list(item_ids))})")
        if not seeds:
            return HistoryResult(rows=[])
        sql = f"""
            WITH RECURSIVE chain(id) AS (
                SELECT i.id FROM memory_items i WHERE ({" OR ".join(seeds)})
                UNION
                SELECT CASE WHEN l.src_item_id = c.id THEN l.dst_item_id ELSE l.src_item_id END
                FROM chain c JOIN memory_links l ON l.link_type = 'supersedes'
                 AND (l.src_item_id = c.id OR l.dst_item_id = c.id)
            ),
            rows AS (
                SELECT i.* FROM memory_items i JOIN chain c ON c.id = i.id WHERE {mandatory}
            ),
            because AS (
                SELECT l.src_item_id AS a, l.dst_item_id AS b FROM memory_links l
                JOIN memory_items o ON o.id = l.dst_item_id AND {other}
                WHERE l.link_type = 'because' AND l.src_item_id IN (SELECT id FROM rows)
                UNION
                SELECT l.dst_item_id, l.src_item_id FROM memory_links l
                JOIN memory_items o ON o.id = l.src_item_id AND {other}
                WHERE l.link_type = 'because' AND l.dst_item_id IN (SELECT id FROM rows)
            )
            SELECT 'row' AS what, r.id, NULL::uuid, r.valid_from, r.valid_to, r.state,
                   (SELECT k.text FROM memory_keys k WHERE k.item_id = r.id
                      AND k.key_kind = 'change' ORDER BY k.created_at LIMIT 1),
                   coalesce(r.valid_from, r.occurred_start, r.mentioned_at, r.created_at) AS at
            FROM rows r
            UNION ALL
            SELECT 'because', a, b, NULL, NULL, NULL, NULL, NULL FROM because
            ORDER BY what DESC, at, 2
        """
        async with self._tx() as s:
            rows = (await s.execute(text(sql), w.params)).all()
        return HistoryResult(
            rows=[
                HistoryRow(
                    item_id=r[1], valid_from=r[3], valid_to=r[4], state=r[5], change_key=r[6]
                )
                for r in rows
                if r[0] == "row"
            ],
            because=[(r[1], r[2]) for r in rows if r[0] == "because"],
        )

    # ------------------------------------------------------------------ conversation

    async def conversation(
        self,
        query: Query,
        *,
        role: Literal["user", "assistant"] | None,
        window: WindowFilter | None,
        exclude_turn: uuid.UUID | None,
        limit: int = 10,
    ) -> list[ConversationHit]:
        """Hybrid search over what was said (``conversation_keys``), best snippet per turn
        and speaker."""
        w = _Where()
        where = ["true"]
        if role is not None:
            where.append(f"c.role = {w.bind(role)}")
        if window is not None and window.start is not None:
            where.append(f"c.said_at >= {w.bind(window.start)}")
        if window is not None and window.end is not None:
            where.append(f"c.said_at < {w.bind(window.end)}")
        if exclude_turn is not None:
            where.append(f"c.turn_id <> {w.bind(exclude_turn)}")
        clause = " AND ".join(where)
        tsq = tsquery_text(query.text)
        lexical = "SELECT NULL::uuid AS id, NULL::real AS score WHERE false"
        if tsq is not None:
            q = w.bind(tsq)
            lexical = f"""
                SELECT c.id, ts_rank_cd(c.tsv, to_tsquery('english', {q}), 32) AS score
                FROM conversation_keys c
                WHERE {clause} AND c.tsv @@ to_tsquery('english', {q})
                ORDER BY score DESC, c.id LIMIT {_POOL}
            """
        dense = "SELECT NULL::uuid AS id, NULL::float AS score WHERE false"
        if query.vector is not None:
            vec = w.bind(vector_literal(query.vector))
            model = w.bind(query.model)
            dense = f"""
                SELECT c.id, 1 - (c.embedding <=> CAST({vec} AS vector)) AS score
                FROM conversation_keys c
                WHERE {clause} AND c.embedding IS NOT NULL AND c.embedding_model = {model}
                ORDER BY c.embedding <=> CAST({vec} AS vector), c.id LIMIT {_POOL}
            """
        sql = f"""
            WITH lex AS (
                SELECT id, score, row_number() OVER (ORDER BY score DESC, id) AS rnk
                FROM ({lexical}) x
            ),
            dns AS (
                SELECT id, score, row_number() OVER (ORDER BY score DESC, id) AS rnk
                FROM ({dense}) x
            ),
            fused AS (
                SELECT coalesce(l.id, d.id) AS id, l.score AS lexical, d.score AS dense,
                       coalesce(1.0 / (:rrf_k + l.rnk), 0) + coalesce(1.0 / (:rrf_k + d.rnk), 0)
                           AS rrf
                FROM lex l FULL OUTER JOIN dns d ON d.id = l.id
            ),
            best AS (
                SELECT DISTINCT ON (c.turn_id, c.role) c.turn_id, c.role, c.seq, c.text,
                       c.said_at, f.lexical, f.dense, f.rrf
                FROM fused f JOIN conversation_keys c ON c.id = f.id
                ORDER BY c.turn_id, c.role, f.rrf DESC
            )
            SELECT turn_id, role, seq, text, said_at, lexical, dense FROM best
            ORDER BY rrf DESC, said_at DESC LIMIT :limit
        """
        async with self._tx() as s:
            rows = (
                await s.execute(text(sql), {**w.params, "rrf_k": self._rrf_k, "limit": limit})
            ).all()
        return [
            ConversationHit(
                turn_id=r[0],
                role=r[1],
                seq=int(r[2]),
                text=r[3],
                said_at=r[4],
                rank=n + 1,
                lexical=float(r[5]) if r[5] is not None else None,
                dense=float(r[6]) if r[6] is not None else None,
            )
            for n, r in enumerate(rows)
        ]


def _set_op(op: SetOp, w: _Where) -> str:
    if op.op == "without":
        other = w.bind(op.other_kind.value if op.other_kind else "episode")
        return f"""NOT EXISTS (
            SELECT 1 FROM memory_entities me1
            JOIN memory_entities me2 ON me2.entity_id = me1.entity_id AND me2.item_id <> i.id
            JOIN memory_items o ON o.id = me2.item_id AND o.status = 'active'
            WHERE me1.item_id = i.id AND me1.role IN ('about', 'at') AND o.kind = {other})"""
    entity = w.bind(op.entity_id)
    return f"""(EXISTS (SELECT 1 FROM memory_entities me WHERE me.item_id = i.id
                        AND me.entity_id = {entity})
            OR EXISTS (
            SELECT 1 FROM memory_entities me1
            JOIN memory_entities me2 ON me2.entity_id = me1.entity_id AND me2.item_id <> i.id
            JOIN memory_entities me3 ON me3.item_id = me2.item_id AND me3.entity_id = {entity}
            JOIN memory_items o ON o.id = me2.item_id AND o.status = 'active'
            WHERE me1.item_id = i.id AND me1.role IN ('about', 'at')))"""


__all__ = ["INVERSE", "SYMMETRIC", "SqlRecallStore", "tsquery_text"]
