# Runbook

What to do when something needs doing to a running stack. The sections marked **tried** were run
on the production-shaped local stack (`make up-prodlike`) in Sprint 3.9, with the output shown;
the AWS ones are stubs that name the sprint that fills them in (NFR-10.10).

Commands run from the repository root. On the local stack `make` talks to the compose services;
on AWS the same CLI runs as an ECS task (S4) and the same Redis flag applies.

## Contents

1. [Kill switch](#1-kill-switch) (tried)
2. [Spend cap reached](#2-spend-cap-reached) (tried)
3. [Provider outage or out of credit](#3-provider-outage-or-out-of-credit) (tried)
4. [Upgrade or downgrade a person](#4-upgrade-or-downgrade-a-person) (tried)
5. [Deploy](#5-deploy) (S4)
6. [Roll back](#6-roll-back) (S4)
7. [Restore the database](#7-restore-the-database) (S8)
8. [Alerts](#8-alerts) (S8)

## 1. Kill switch

**When:** something is spending money it shouldn't, a provider is misbehaving, or you need every
model call to stop now. It needs no deploy and no restart.

```sh
make kill-switch on        # stop all new model work
make kill-switch STATE=status
make kill-switch off       # resume
```

**What happens.** Every api and worker process re-reads the flag at most every 2 seconds. From
then: a new message is stored as a turn with a `blocked` step and gets a template reply
("New answers are paused for everyone right now. You can still browse your memory, check
Upcoming, undo, and open the glass box."), with no model call. A turn already running finishes
(so nothing is left half-written). Background jobs that need a model are put back on the queue
for five minutes later, not dropped. Browsing, Upcoming, undo and the glass box keep working.

**Check it.** `GET /v1/me/usage` returns `read_only: true` with `read_only_reason: "kill_switch"`;
the composer shows the same words. `make spend-now` should stop moving.

**Also:** `KILL_SWITCH=true` in the environment sets the value a fresh process starts with; the
Redis flag, once set, wins. If Redis itself is down the gate fails closed (turns are paused) and
`/readyz` fails, which is the right behaviour: nothing can be counted.

## 2. Spend cap reached

**When:** the daily (`SPEND_CAP_DAILY_USD`, $0.50) or monthly (`SPEND_CAP_MONTHLY_USD`, $5) cap
for the whole app is reached, or a warning at 80% (`spend.cap_warning` in the logs).

```sh
make spend-now             # today, this month, and each provider against its credit
```

**What happens.** At the cap new turns stop app-wide with "Today's spending limit for the whole
app is used up" and no model call. The cap resets at 00:00 UTC (day) or on the 1st (month); the
counters are rebuilt from the usage ledger every 10 minutes, so a restart or a drift never
loses count.

**To let people carry on now:** raise the cap and restart the api and worker tasks
(`SPEND_CAP_DAILY_USD=1.00`); that is a decision to spend more, so check `make spend-now` and the
provider dashboards first. **To find out where the money went:** the ledger has every call with
its step, model and turn (`usage_ledger`); background usage is marked `system`.

## 3. Provider outage or out of credit

**Outage.** Each step has a fallback on the other provider at the same price level (Luna → Haiku,
Haiku → GPT-5.4 mini, GPT-5.4 mini → Haiku). A failing provider is retried with backoff, then its
circuit opens and its steps go straight to the fallback. Nothing falls back to a model dearer
than the next rung, so an outage does not make turns dearer than about 2× (measured in R.4:
$0.0105 a turn with OpenAI unreachable, against $0.0054). If both providers are down the turn
fails cleanly with "The model provider is unavailable right now", no half-written memory.

**Try it:** `OPENAI_BASE_URL=http://127.0.0.1:9 make up-prodlike` points OpenAI at a dead host;
send a message and open the glass box: intent, plan and rerank show "fallback from openai:…".

**Out of credit.** When a provider answers "credit balance is too low" (Anthropic 402 or 400,
OpenAI `insufficient_quota`), the app marks it out for an hour and skips it: its steps use their
fallback. When both are out the app is read-only, with "The model providers' credit for this app
is used up." To stop earlier, set `PROVIDER_CREDIT_USD_ANTHROPIC` / `_OPENAI` below the real
balance, counted from `PROVIDER_CREDIT_SINCE`. After topping up: raise the credit variables and
restart; the marks expire on their own.

## 4. Upgrade or downgrade a person

```sh
make set-tier EMAIL=someone@example.com TIER=premium     # guest | standard | premium
```

Guest is Auto only, $0.75 lifetime; standard picks Luna, GPT-5.4 mini or Haiku 4.5, $2.50;
premium adds Sonnet 5, $4.00. Every change is written to `tier_changes` (who, when, from, to).
The person's quota and picker follow on their next turn.

## 5. Deploy

*Stub. S4 fills this in from ADR-0012: build and push the two images (tagged by git SHA) from
CI, apply Terraform for the environment, run the migrate task, then roll the api and worker
services; the migrate task and the services use the same image tag.*

## 6. Roll back

*Stub. S4: redeploy the previous image tag; migrations are written so the previous release runs
on the new schema (one release of compatibility, NFR-10.4; CONTRIBUTING.md has the two-step
rule), so a rollback never needs a schema downgrade.*

## 7. Restore the database

*Stub. S8: RDS point-in-time restore into a new instance, repoint `DATABASE_URL` in Secrets
Manager, roll the services.*

## 8. Alerts

*Stub. S8: the `spend.cap_warning` log event, provider error rates and readiness failures become
alarms.*
