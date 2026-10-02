"""Where a turn's time goes (ledger 52), worked out from a stored run and nothing else.

Only a call that went to a provider says anything about time: a cached call replays a recorded
answer in no time, and an estimate is not a measurement. So the profile counts fresh calls only,
says how many it left out, and never presents a run that was mostly replayed as a timing. The steps
of a recall turn run one after another, so the sum of their medians is a fair picture of the
critical path; a run's own total median (over the cases that made no cached call) is printed beside
it to show how far the two agree.
"""

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from secondmind.evals.runs import RunRecord, pct

# A candidate the rerank scores at or above this stays in play (``rerank_min_score``).
KEEP_SCORE = 0.5
# Depths to ask "how many calls had something worth keeping further down than this?" about.
DEPTHS = (5, 10, 15)

# The order a recall turn takes its steps in: the turn graph runs intent, then the planner, then
# the query embedding, the rerank and the answer, each waiting for the one before.
RECALL_CHAIN = ("intent", "plan", "embed", "rerank", "answer")


@dataclass(frozen=True, slots=True)
class StepTiming:
    step: str
    fresh: int  # calls that went to a provider and were timed
    left_out: int  # calls replayed from the cache, estimated or failed
    p50_ms: float
    p95_ms: float


def step_timings(run: RunRecord) -> list[StepTiming]:
    """One row per step that has at least one fresh call, in the recall chain's order first and
    then by median, longest first."""
    fresh: dict[str, list[float]] = defaultdict(list)
    left_out: dict[str, int] = defaultdict(int)
    for case in run.cases:
        for call in case.calls:
            step = str(call["step"])
            usable = (
                call.get("timed", True)
                and not call.get("cache_hit")
                and not call.get("estimated")
                and not call.get("failed")
                and call.get("latency_ms") is not None
            )
            if usable:
                fresh[step].append(float(call["latency_ms"]))
            else:
                left_out[step] += 1
    rows = [
        StepTiming(
            step=step,
            fresh=len(values),
            left_out=left_out[step],
            p50_ms=pct(values, 0.5) or 0.0,
            p95_ms=pct(values, 0.95) or 0.0,
        )
        for step, values in fresh.items()
    ]
    order = {step: n for n, step in enumerate(RECALL_CHAIN)}
    return sorted(rows, key=lambda r: (order.get(r.step, len(order)), -r.p50_ms))


def chain_p50_ms(rows: Sequence[StepTiming]) -> float:
    """The sum of the medians of the recall chain's steps that were measured."""
    return sum(r.p50_ms for r in rows if r.step in RECALL_CHAIN)


def total_p50_ms(run: RunRecord) -> float | None:
    totals = [float(c.latency_ms) for c in run.cases if c.fresh and c.latency_ms is not None]
    return pct(totals, 0.5)


def render_profile(run: RunRecord) -> str:
    """A Markdown table (it reads as plain text in a terminal too) and the reading of it."""
    rows = step_timings(run)
    timed = sum(1 for c in run.cases if c.fresh)
    chain = chain_p50_ms(rows)
    total = total_p50_ms(run)
    lines = [
        f"`{run.run_id}` · {run.suite} · routing {run.routing} · {len(run.cases)} cases, "
        f"{timed} of them timed end to end (no cached call)",
        "",
        "| Step | Fresh calls | Left out | p50 | p95 | Share of the chain |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        share = f"{r.p50_ms / chain:.0%}" if r.step in RECALL_CHAIN and chain else "n/a"
        lines.append(
            f"| {r.step} | {r.fresh} | {r.left_out} | {r.p50_ms / 1000:.2f} s "
            f"| {r.p95_ms / 1000:.2f} s | {share} |"
        )
    lines += ["", _reading(chain, total, timed)]
    return "\n".join(lines)


def _reading(chain_ms: float, total_ms: float | None, timed: int) -> str:
    chain = " → ".join(RECALL_CHAIN)
    text = (
        f"The steps of a recall turn run in order ({chain}): "
        f"the medians add up to {chain_ms / 1000:.1f} s"
    )
    if total_ms is None:
        return text + ". No case in this run was timed end to end."
    return (
        text
        + f"; the run's own median from question to end of answer was {total_ms / 1000:.1f} s "
        + f"over {timed} cases. 'Left out' counts calls replayed from the cache, estimated or "
        + "failed: they say nothing about a provider's time."
    )


@dataclass(frozen=True, slots=True)
class RerankAnatomy:
    """What the slowest, dearest step of a recall turn is made of."""

    calls: int
    one_candidate: int  # calls that judged a single candidate
    one_candidate_kept: int  # ...and scored it high enough that skipping the call changes nothing
    judged_p50: float  # candidates judged per call
    output_tokens_p50: float
    fixed_ms: float  # the fit's intercept: time that does not grow with the output
    ms_per_output_token: float
    beyond: dict[int, int] = field(default_factory=dict)  # depth -> calls with a keeper below it


def rerank_anatomy(run: RunRecord) -> RerankAnatomy | None:
    """Worked out from the scores each rerank call returned (``c1`` is the top of the fused order)
    and, for the time fit, from its fresh calls only. None when the run has no rerank scores."""
    judged: list[int] = []
    tokens: list[float] = []
    deepest: list[int] = []
    single = single_kept = 0
    fit: list[tuple[float, float]] = []
    for case in run.cases:
        for call in case.calls:
            output = call.get("output")
            scores = output.get("scores") if isinstance(output, dict) else None
            if call["step"] != "rerank" or not scores:
                continue
            judged.append(len(scores))
            tokens.append(float(call["output_tokens"]))
            kept = [
                int(str(s["id"])[1:])
                for s in scores
                if float(s["score"]) >= KEEP_SCORE and str(s["id"])[1:].isdigit()
            ]
            deepest.append(max(kept, default=0))
            if len(scores) == 1:
                single += 1
                single_kept += bool(kept)
            if call.get("timed", True) and not call.get("cache_hit") and call.get("latency_ms"):
                fit.append((float(call["output_tokens"]), float(call["latency_ms"])))
    if not judged:
        return None
    slope, intercept = _line(fit)
    return RerankAnatomy(
        calls=len(judged),
        one_candidate=single,
        one_candidate_kept=single_kept,
        judged_p50=statistics.median(judged),
        output_tokens_p50=statistics.median(tokens),
        fixed_ms=intercept,
        ms_per_output_token=slope,
        beyond={d: sum(1 for x in deepest if x > d) for d in DEPTHS},
    )


def _line(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Least squares through (x, y); (0, mean) when x never varies."""
    if len(points) < 2:
        return 0.0, statistics.mean(y for _, y in points) if points else 0.0
    mx = statistics.mean(x for x, _ in points)
    my = statistics.mean(y for _, y in points)
    spread = sum((x - mx) ** 2 for x, _ in points)
    if not spread:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in points) / spread
    return slope, my - slope * mx


def render_rerank(run: RunRecord) -> str:
    a = rerank_anatomy(run)
    if a is None:
        return "This run has no rerank scores to read."
    deep = "; ".join(f"beyond rank {d}: {n}" for d, n in a.beyond.items())
    return "\n".join(
        [
            f"- **{a.calls} rerank calls**, each judging a median of {a.judged_p50:.0f} candidates "
            f"and writing a median of {a.output_tokens_p50:.0f} tokens.",
            f"- **Time is mostly output:** about {a.fixed_ms / 1000:.1f} s fixed plus "
            f"{a.ms_per_output_token:.1f} ms per output token (a straight-line fit over the "
            "fresh calls).",
            f"- **One candidate:** {a.one_candidate} calls judged exactly one, and in "
            f"{a.one_candidate_kept} of them it scored {KEEP_SCORE} or more, so skipping those "
            f"calls would have changed the outcome in {a.one_candidate - a.one_candidate_kept} of "
            f"{a.calls}.",
            f"- **How deep the keepers sit** (calls with a candidate scored {KEEP_SCORE} or more "
            f"below a rank): {deep}.",
        ]
    )


START = (
    "<!-- profile:start (generated by `python -m secondmind.evals latency RUN --into FILE`; "
    "do not edit) -->"
)
END = "<!-- profile:end -->"


def sync_doc(text: str, run: RunRecord) -> str:
    """``text`` with the block between the profile markers replaced by this run's profile, so a
    document that quotes timings quotes them from a run file and cannot drift from it."""
    start, end = text.find(START), text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"the document needs the markers {START!r} and {END!r}")
    body = f"{render_profile(run)}\n\n{render_rerank(run)}"
    return f"{text[:start]}{START}\n{body}\n{text[end:]}"
