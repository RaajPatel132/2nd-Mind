# Documentation

Where to start depends on how long you have.

**Five minutes.** The [README](../README.md) for what it is and how well it works, then
[`architecture.md`](architecture.md) for the diagrams: the system, one turn, saving, recalling and
the data.

**Fifteen minutes.** Add the five decision records that carry the most weight:
[0018](adr/0018-memory-model.md) (the memory model),
[0019](adr/0019-reconciliation.md) (how a new fact meets old ones),
[0026](adr/0026-hybrid-search.md) (finding things),
[0003](adr/0003-workspace-isolation-rls.md) (isolation) and
[0037](adr/0037-content-is-data.md) (why a pasted page can't steer the agent).

**An hour.** Read the code where those decisions land, in this order:

| Idea | Where it is |
|---|---|
| One turn: the graph that routes a message | [`agent/graph.py`](../backend/src/secondmind/agent/graph.py) |
| Saving: extract, resolve, reconcile, enrich | [`ingestion/`](../backend/src/secondmind/ingestion/) |
| The only way a memory changes | [`memory/`](../backend/src/secondmind/memory/) |
| What may be written | [`policy/rules.py`](../backend/src/secondmind/policy/rules.py) |
| Recalling: the plan, the tools, the fusion | [`retrieval/plan.py`](../backend/src/secondmind/retrieval/plan.py) |
| Which model serves a step | [`providers/router.py`](../backend/src/secondmind/providers/router.py), [`config/models.yaml`](../backend/config/models.yaml) |
| The one door a pasted link goes through | [`links/adapters/fetcher.py`](../backend/src/secondmind/links/adapters/fetcher.py) |
| Dollars: ledger, quotas, the spend gate | [`metering/`](../backend/src/secondmind/metering/) |
| How the steps are shown | [`frontend/src/trail/steps.ts`](../frontend/src/trail/steps.ts) |
| How quality is measured | [`evals/`](../backend/src/secondmind/evals/), cases in [`backend/evals/cases/`](../backend/evals/cases/), runs in `backend/evals/runs/` |
| How it is tested | [`backend/tests/`](../backend/tests/), [`frontend/e2e/`](../frontend/e2e/) |

## The documents

| | What it answers |
|---|---|
| [`architecture.md`](architecture.md) | How it is put together, and where each kind of trust is enforced |
| [`PRD.md`](PRD.md) | What it must do and why: users, requirements, how success is measured |
| [`adr/`](adr/README.md) | Why each choice was made, one page each, with the alternatives that lost |
| [`design/system.md`](design/system.md) | Ink, the design system: tokens, type, motion, the Trail, the product's words |
| [`case-studies/model-routing.md`](case-studies/model-routing.md) | How the models were chosen by measurement on a small budget, what it cost, and what the numbers do not show |
| [`performance.md`](performance.md) | Where a recall turn's time goes, by step, from a stored run |
| [`runbook.md`](runbook.md) | How it is run: deploys, spend controls, backups, what to do when |
| [`media/`](media/) | The pictures in the README, remade by script ([how](../README.md#develop)) |
