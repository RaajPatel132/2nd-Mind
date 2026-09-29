"""``make smoke-live``: drive the API through the S3 demo's ten steps and the S2 examples on real
models, and check structure, not wording (R.6).

It signs in as a fresh user (the staging access code if the stack asks for one), seeds the recall
fixture, and asks the questions of the demo script. What it checks is what code decides: the
shape the plan chose, that citations exist and point at the expected memories, that an
unanswerable question made no answer-model call, that a trigger fired, that a correction applied
and an undo put things back, and that every turn reports its usage and a trace. What the model
words is never compared. It prints a pass/fail table, the cost and a run id, writes the run file
(``backend/evals/runs/smoke/``) and adds the cost to the live spend book.

    SMOKE_BASE_URL=http://localhost:8080 SMOKE_ACCESS_CODE=… make smoke-live
"""

import argparse
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from secondmind.evals.runs import (
    CaseRecord,
    RunRecord,
    git_state,
    new_run_id,
    write_run,
)
from secondmind.evals.spend import SpendBook


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Turn:
    data: dict[str, Any]
    events: list[dict[str, Any]]
    latency_ms: int

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def reply(self) -> str:
        return str(self.data.get("output") or "")

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e["event"] for e in self.events if e["event"]["type"] == kind]

    @property
    def shapes(self) -> list[str]:
        return [sq["shape"] for r in self.of("retrieval") for sq in r["sub_queries"]]

    @property
    def abstained(self) -> bool:
        return any(sq["abstained"] for r in self.of("retrieval") for sq in r["sub_queries"])

    @property
    def answer_calls(self) -> int:
        return sum(1 for c in self.of("model_call") if c["step"] == "answer")

    @property
    def cost(self) -> Decimal:
        return Decimal(str(self.data["usage"]["cost_usd"]))

    def spent_by_provider(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(Decimal)
        for call in self.of("model_call"):
            out[call["provider"]] += Decimal(str(call["usage"]["cost_usd"]))
        return out


class Smoke:
    def __init__(self, base: str, access_code: str | None) -> None:
        self.http = httpx.Client(base_url=base, timeout=180.0)
        self.access_code = access_code
        self.workspace = ""
        self.turns: list[Turn] = []
        self.checks: list[Check] = []

    # ------------------------------------------------------------------ plumbing

    def sign_in(self) -> None:
        email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
        body: dict[str, Any] = {"email": email}
        if self.access_code:
            body["access_code"] = self.access_code
        response = self.http.post("/v1/auth/dev-login", json=body)
        response.raise_for_status()
        # A Secure cookie is not sent back over plain http by clients; send it by hand.
        cookie = response.headers.get("set-cookie", "").split(";", 1)[0]
        self.http.headers["Cookie"] = cookie
        self.workspace = str(self.http.get("/v1/me").json()["workspaces"][0]["id"])
        self.http.post("/v1/dev/seed-recall").raise_for_status()

    def say(self, message: str, *, free: bool = False) -> Turn:
        started = time.perf_counter()
        response = self.http.post(
            f"/v1/workspaces/{self.workspace}/turns", json={"message": message}
        )
        response.raise_for_status()
        frames: list[tuple[str, dict[str, Any]]] = []
        for block in response.text.split("\n\n"):
            fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
            if "event" in fields and "data" in fields:
                frames.append((fields["event"], json.loads(fields["data"])))
        done = next(d for n, d in frames if n in ("turn.completed", "turn.failed"))
        turn_id = done["turn"]["id"]
        events = self.http.get(f"/v1/turns/{turn_id}/events").json()["events"]
        turn = Turn(done["turn"], events, round((time.perf_counter() - started) * 1000))
        self.turns.append(turn)
        self.turn_checks(message, turn, free=free)
        return turn

    def turn_checks(self, message: str, turn: Turn, *, free: bool = False) -> None:
        usage = turn.data["usage"]
        tokens = usage["input_tokens"] + usage["output_tokens"] + usage["cached_input_tokens"]
        self.expect(
            f"usage on: {message[:40]}",
            turn.data["status"] == "completed" and (free or (tokens > 0 and turn.cost > 0)),
            f"{turn.data['status']}, {tokens} tokens, ${turn.cost}",
        )
        trace = turn.data.get("trace") or {}
        ok, detail = self.trace_ok(trace, turn, message)
        self.expect(f"trace on: {message[:40]}", ok, detail)

    def trace_ok(self, trace: dict[str, Any], turn: Turn, message: str) -> tuple[bool, str]:
        """The trace id resolves in Langfuse and holds every model call with tokens and cost, and
        (content tracing being off) none of the person's words. Skipped without keys."""
        url = trace.get("url")
        public = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret = os.environ.get("LANGFUSE_SECRET_KEY")
        if trace.get("status") not in ("recorded", "pending", "disabled"):
            return False, f"trace status {trace.get('status')}"
        if not (url and public and secret):
            return True, "not resolved (no trace url or Langfuse keys)"
        host = os.environ.get("SMOKE_LANGFUSE_HOST", "http://localhost:3000")
        trace_id = str(url).rstrip("/").rsplit("/", 1)[-1]
        calls = len(turn.of("model_call"))
        found: list[dict[str, Any]] = []
        for _ in range(15):  # ingestion is asynchronous
            response = httpx.get(
                f"{host}/api/public/v2/observations",
                params={
                    "traceId": trace_id,
                    "type": "GENERATION",
                    "fields": "core,basic,io,model,usage",
                    "limit": 100,
                },
                auth=(public, secret),
                timeout=20,
            )
            found = response.json().get("data", []) if response.status_code == 200 else []
            if len(found) >= calls:
                break
            time.sleep(2)
        if len(found) < calls:
            return False, f"{len(found)} of {calls} generations in the trace"
        bare = [o for o in found if not (o.get("usageDetails") or {}).get("total")]
        if bare:
            return False, f"{len(bare)} generations without tokens"
        dump = json.dumps(found)
        leaked = [w for w in (message, "17-38-02") if len(w) > 12 and w in dump]
        return (not leaked), f"message text in the trace: {leaked}" if leaked else "ok"

    def expect(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, bool(ok), detail))

    def cited_text(self, turn: Turn) -> str:
        """The titles and texts of the memories a reply cites, lowercased, to map citations to
        the fixture's items without reading the model's words."""
        parts: list[str] = []
        for event in turn.of("citations"):
            for citation in event["citations"]:
                if citation["item_id"]:
                    item = self.http.get(f"/v1/items/{citation['item_id']}").json()["item"]
                    parts.append(f"{item['title']} {item['text']}")
                else:
                    parts.append(citation["title"])
        return " ".join(parts).lower()

    def cites(self, turn: Turn, *needles: str) -> bool:
        text = self.cited_text(turn)
        return bool(text) and all(n.lower() in text for n in needles)

    # ------------------------------------------------------------------ the S3 demo, in order

    def run(self) -> None:
        self.step_1_latest()
        self.step_2_count_and_correction()
        self.step_3_relation_hop()
        self.step_4_weekend()
        self.step_5_conversation()
        self.step_6_abstain()
        self.step_7_soft_only()
        self.step_8_trigger()
        self.step_9_relation_correction()
        self.step_10_upcoming_snooze()
        self.s2_examples()

    def step_1_latest(self) -> None:
        t = self.say("Where do I live?")
        self.expect("1 shape latest", "latest" in t.shapes, str(t.shapes))
        self.expect("1 cites the Pune fact", self.cites(t, "pune"), self.cited_text(t)[:80])

    def step_2_count_and_correction(self) -> None:
        t = self.say("How many runs did I do in September?")
        offers = [
            sq["count_check"]
            for r in t.of("retrieval")
            for sq in r["sub_queries"]
            if sq["count_check"] and sq["count_check"]["offer"]
        ]
        aggregates = [
            sq["aggregate"]["value"]
            for r in t.of("retrieval")
            for sq in r["sub_queries"]
            if sq["aggregate"]
        ]
        self.expect("2 exact count from aggregate", bool(aggregates), str(aggregates))
        self.expect("2 offers to file the look-alikes", bool(offers), "no count_check offer")
        if not (offers and aggregates):
            return
        fix = self.say("yes")
        ops = [e["op"] for d in fix.of("memory_diff") for e in d["entries"]]
        self.expect("2 'yes' is a correction (updated entries)", "updated" in ops, str(ops))
        again = self.say("How many runs did I do in September?")
        after = [
            sq["aggregate"]["value"]
            for r in again.of("retrieval")
            for sq in r["sub_queries"]
            if sq["aggregate"]
        ]
        self.expect(
            "2 the count went up",
            bool(after) and after[0] > aggregates[0],
            f"{aggregates} -> {after}",
        )

    def step_3_relation_hop(self) -> None:
        t = self.say("What does Nisha's husband like?")
        self.expect("3 cites Rohan's preferences", self.cites(t, "rohan"), self.cited_text(t)[:80])

    def step_4_weekend(self) -> None:
        t = self.say("I'm free on Saturday 10 October, what should I learn?")
        self.expect("4 cites the learn intentions", self.cites(t, "rust"), self.cited_text(t)[:80])

    def step_5_conversation(self) -> None:
        t = self.say("The books you suggested on Tuesday 29 September?")
        kinds = [c["kind"] for e in t.of("citations") for c in e["citations"]]
        self.expect("5 cites a past turn", "turn" in kinds, str(kinds))

    def step_6_abstain(self) -> None:
        t = self.say("What's Kabir's shoe size?")
        self.expect("6 abstains", t.abstained, "did not abstain")
        self.expect("6 makes no answer-model call", t.answer_calls == 0, str(t.answer_calls))

    def step_7_soft_only(self) -> None:
        t = self.say("What did I read about sleep?")
        self.expect("7 cites the sleep note", self.cites(t, "sleep"), self.cited_text(t)[:80])

    def step_8_trigger(self) -> None:
        self.say("Having lunch with Nisha tomorrow")
        # The reminder about Nisha fires the first time she comes up, which in this order can be
        # step 3: the diff of the turn that fired it holds a "reminder: …" entry.
        fired = [
            n + 1
            for n, turn in enumerate(self.turns)
            if any(
                e["title"].startswith("reminder:")
                for d in turn.of("memory_diff")
                for e in d["entries"]
            )
        ]
        self.expect("8 the trigger fired once Nisha came up", bool(fired), "no reminder entry")

    def step_9_relation_correction(self) -> None:
        fix = self.say("No, Rohan is Nisha's cousin, not her husband")
        ops = [e["op"] for d in fix.of("memory_diff") for e in d["entries"]]
        self.expect("9 the relation is corrected", bool(ops), str(ops))
        again = self.say("What does Nisha's husband like?")
        self.expect(
            "9 the husband question no longer finds Rohan",
            again.abstained or not self.cites(again, "rohan"),
            self.cited_text(again)[:80],
        )

    def step_10_upcoming_snooze(self) -> None:
        upcoming = self.http.get(f"/v1/workspaces/{self.workspace}/upcoming?days=30").json()
        days = upcoming["days"]
        self.expect("10 Upcoming lists days", bool(days), str(len(days)))
        reminders = [e for d in days for e in d["entries"] if e["trigger_id"]]
        if not reminders:
            self.expect("10 a reminder to snooze", False, "none in the next 30 days")
            return
        snooze = self.http.post(
            f"/v1/triggers/{reminders[0]['trigger_id']}/snooze",
            json={"date_expression": "in 3 days at 7pm"},
        )
        self.expect("10 snooze is its own turn", snooze.status_code == 200, str(snooze.status_code))
        if snooze.status_code == 200:
            undo = self.http.post(f"/v1/turns/{snooze.json()['id']}/undo")
            self.expect(
                "10 undo puts the snooze back", undo.status_code == 200, str(undo.status_code)
            )

    # ------------------------------------------------------------------ the S2 examples

    def s2_examples(self) -> None:
        t = self.say("My favourite tea is jasmine")
        self.expect(
            "S2 a save adds a memory",
            any(e["op"] == "added" for d in t.of("memory_diff") for e in d["entries"]),
            t.reply[:60],
        )
        undo = self.http.post(f"/v1/turns/{t.id}/undo")
        self.expect("S2 undo removes what the save added", undo.status_code == 200)
        secret = self.say("My gym locker combination is 17-38-02", free=True)
        stored = json.dumps(secret.events) + secret.reply
        self.expect(
            "S2 a secret is refused and absent", "17-38-02" not in stored, secret.reply[:60]
        )
        hypo = self.say("If I get the job in Delhi I'll move there")
        facts = []
        for diff in hypo.of("memory_diff"):
            for entry in diff["entries"]:
                if entry["op"] == "added" and entry["item_id"]:
                    item = self.http.get(f"/v1/items/{entry['item_id']}").json()["item"]
                    if item["kind"] in ("fact", "preference"):
                        facts.append(item["title"])
        self.expect("S2 a hypothetical is not stored as a fact", not facts, str(facts))


def main() -> int:
    parser = argparse.ArgumentParser(prog="smoke-live")
    parser.add_argument(
        "--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://localhost:8080")
    )
    parser.add_argument("--access-code", default=os.environ.get("SMOKE_ACCESS_CODE"))
    parser.add_argument("--note", default=None)
    args = parser.parse_args()

    smoke = Smoke(args.base_url, args.access_code)
    started = datetime.now(UTC)
    smoke.sign_in()
    meta = smoke.http.get("/v1/meta").json()
    smoke.expect("live providers", meta["provider_mode"] == "live", meta["provider_mode"])
    smoke.expect("no provider tag in the UI meta", meta["provider_mode"] != "fake")
    try:
        smoke.run()
    except Exception as exc:  # a broken step is a failed check, and the table still prints
        smoke.expect("the run completed", False, f"{type(exc).__name__}: {exc}")

    width = max(len(c.name) for c in smoke.checks)
    for c in smoke.checks:
        sys.stdout.write(
            f"{'PASS' if c.ok else 'FAIL'}  {c.name:<{width}}  {c.detail if not c.ok else ''}\n"
        )
    total = sum((t.cost for t in smoke.turns), Decimal(0))
    passed = sum(c.ok for c in smoke.checks)
    by_provider: dict[str, Decimal] = defaultdict(Decimal)
    for t in smoke.turns:
        for provider, amount in t.spent_by_provider().items():
            by_provider[provider] += amount

    sha, dirty = git_state()
    run = RunRecord(
        run_id=new_run_id(sha, started),
        suite="smoke",
        mode="live",
        routing="configured",
        started_at=started,
        finished_at=datetime.now(UTC),
        git_sha=sha,
        git_dirty=dirty,
        config_hash=meta["config_hash"],
        price_version=meta["price_version"],
        prompt_versions={},
        models={r["step"]: f"{r['provider']}:{r['model']}" for r in meta["routes"]},
        fallbacks={},
        batch=os.environ.get("LIVE_BATCH"),
        note=args.note,
        cases=[
            CaseRecord(
                id=f"{i + 1:02d}", title=c.name, passed=c.ok, failures=[] if c.ok else [c.detail]
            )
            for i, c in enumerate(smoke.checks)
        ],
    )
    path = write_run(run)
    book = SpendBook.load()
    book.record(
        run_id=run.run_id,
        suite="smoke",
        spent_by_provider={p: a for p, a in by_provider.items() if p != "fake"},
        budget=Decimal(0),
        batch=run.batch,
        note=args.note,
    )
    sys.stdout.write(
        f"\n{passed}/{len(smoke.checks)} checks passed; {len(smoke.turns)} turns, "
        f"cost ${total:.4f}\n"
        f"run {run.run_id} -> {path}\n"
    )
    return 0 if passed == len(smoke.checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
