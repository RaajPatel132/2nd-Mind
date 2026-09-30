# Runbook

What to do when something needs doing to a running stack. The sections marked **tried** were run
for real, with the output shown: §1 to §4 on the rehearsal stack (`make up-prodlike`) on
2026-09-29, §5 to §7 on this machine on 2026-09-30 against a sandbox compose project and the
rehearsal's database. What needs AWS credentials (the SSM and GitHub parts) is yours, and the
guide's Part 3 teaches each one as an exercise (NFR-10.10).

Commands run from the repository root. On the local stack `make` talks to the compose services
(add `STACK=prodlike` to address the rehearsal stack instead). In production the same admin CLI
runs in the api container over SSM: `make prod-admin CMD="kill-switch on"`, and the same Redis
flag applies.

## Contents

1. [Kill switch](#1-kill-switch) (tried)
2. [Spend cap reached](#2-spend-cap-reached) (tried)
3. [Provider outage or out of credit](#3-provider-outage-or-out-of-credit) (tried)
4. [Upgrade or downgrade a person](#4-upgrade-or-downgrade-a-person) (tried)
5. [Deploy](#5-deploy) (tried locally)
6. [Roll back](#6-roll-back) (tried locally)
7. [Restore the database](#7-restore-the-database) (tried locally)
8. [Alerts](#8-alerts) (S8)
9. [The host](#9-the-host) (yours to run)

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

**Tried** (`make kill-switch on STACK=prodlike`, then a message):

```text
kill switch is ON (from the runtime flag)
Every api and worker process refuses new model work within a few seconds.
usage: {'tier': 'standard', 'limit_usd': 2.5, 'used_usd': 0.0, 'read_only': True, 'read_only_reason': 'kill_switch'}
turn: completed | New answers are paused for everyone right now. You can still browse your memory, ...
blocked: kill_switch limit None used None            cost: 0.0
kill switch is off (from the runtime flag)
```

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

**Tried** (`SPEND_CAP_DAILY_USD=0.005` on the api and worker, with $0.43 already spent today):

```text
today   $0.4253 of $0.00
usage: {'tier': 'standard', 'limit_usd': 2.5, 'used_usd': 0.0, 'read_only': True, 'read_only_reason': 'daily_cap'}
turn: completed | Today's spending limit for the whole app is used up. You can still browse ...
blocked: daily_cap limit 0.005 used 0.4252776         cost: 0.0
```

The rehearsal overlay pins the real caps ($0.50 a day, $5 a month); a developer's `.env` may
carry larger ones for eval runs, so check `make spend-now` shows the caps you expect.

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

**Tried** (a compose override setting `OPENAI_BASE_URL=http://127.0.0.1:9` on the api and worker,
then "I like oolong tea"): the turn completed, and the glass box shows the fallbacks.

```text
model_call: intent anthropic:claude-haiku-4-5 fallback={'from_provider': 'openai', 'from_model': 'gpt-6-luna', 'reason': 'connection; connection; connection'}
model_call: extract anthropic:claude-haiku-4-5
model_call: enrich anthropic:claude-haiku-4-5 fallback={... 'reason': 'skipped: breaker open'}
cost: 0.010825   (about $0.0087 when OpenAI is up)
```

The first call retried three times, then the breaker opened and the next step skipped straight to
Haiku. **Known gap:** embeddings have only OpenAI behind them, so during an OpenAI outage a save
completes and is found by its text keys, but its vector keys are not made (no `embed` call in
that turn); ledger row 46 in Sprint 3.9's ledger tracks re-embedding on recovery.

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

**Tried** (`make set-tier EMAIL=… TIER=premium STACK=prodlike`, then back):

```text
trial-3bf87c@example.com: standard -> premium (by app (admin CLI), at 2026-09-29T17:16:01+00:00)
Their quota and model picker follow it on their next turn.
```

## 5. Deploy

**When:** a change is ready. Deploying is CI's job: a push to `main` that passes CI deploys it, and
until launch a push to a `sprint-*` branch does too while the repository variable
`DEPLOY_SPRINT_BRANCHES` is `true`.

**What happens** (`.github/workflows/ci.yml`, then `deploy.yml`, then `infra/host/deploy.sh`):

1. CI builds the arm64 images, scans them and, on `main` or a `sprint-*` push, pushes
   `ghcr.io/<owner>/secondmind-api` and `-web`, tagged with the first 12 characters of the git SHA.
   A tag is never overwritten.
2. The deploy job (environment `production`, one at a time) checks those images exist, assumes the
   deploy role through GitHub OIDC, and sends the `secondmind-deploy` SSM document to the host.
3. On the host, as root: check out the SHA, then `deploy.sh <sha>`: fetch the env file from SSM,
   pull the images, start Postgres and Redis if they are not up, run the migration as a one-off,
   start api, worker, web and caddy on the new tag, and wait until `/readyz` answers and
   `/v1/meta` reports this release.
4. From the runner: `scripts/check-deployed.sh` against the public URL (liveness, readiness, the
   release, headers on the SPA and the API, a cross-site POST refused). No model calls.

**A deploy is a short blip, not an outage.** The edge checks the web tier's `/readyz` every second,
marks it down while the api restarts, and holds requests for up to 15 s until it is back. Measured
on a stub stack behind the real Caddy and nginx (`tests/integration/test_stream_through_nginx.py`):
with requests arriving steadily, 3.3 seconds with no api behind the web tier cost nobody an error
(71 requests, none failed), and the longest wait was about 4 s. A single request after a quiet
spell can still get a 502 in the first second or two, before the edge has noticed. A turn in flight
finishes within `SHUTDOWN_GRACE_S` (30 s; the container gets 45 s); the worker finishes its job or
puts it back on the queue, which is covered by a test against a real worker and Redis
(`tests/integration/test_worker_requeue.py`). **Not yet seen on the live host:** the slowed turn and
slowed job checks (guide Part 3, exercise 1, with a long message in flight).

**Tried** (`infra/host/deploy.sh` against a sandbox compose project on the rehearsal's images,
`SKIP_PULL=1`, 2026-09-30; the AWS parts of the path, SSM and OIDC, were not exercised):

```text
==> 16:41:45 up
==> 16:41:53 release b5e88c310ab0 is serving                        # first deploy: 23 s in all

# a release whose api never becomes healthy (READY_TIMEOUT_S=25)
==> 16:44:28 release bad000000000 (previous: b5e88c310ab0); migrate=1
dependency failed to start: container secondmind-api-1 is unhealthy
release bad000000000 did not become ready in 25s
==> 16:44:33 putting b5e88c310ab0 back
==> 16:45:25 b5e88c310ab0 is serving again                         # /v1/meta reports b5e88c310ab0

# a release whose migration raises
migration failed: nothing was changed, b5e88c310ab0 keeps serving   # current release unchanged
```

A first version of the script died at "dependency failed to start" without putting the previous
release back; the trial found it and the script now treats a failed start and a release that never
answers as the same case.

**If a deploy fails:** read the job's output (GitHub → Actions → deploy → *run the deploy document*
prints the host's output), or on AWS: Systems Manager → Run Command → Command history. Then
[roll back](#6-roll-back) if the site is in a bad state. The previous release is put back
automatically when a release doesn't become ready, so the usual cause of a red deploy is the
*images for this release exist* check (a private GHCR package, or CI not having pushed that SHA).

## 6. Roll back

**When:** the newest release is wrong and the previous one was right.

```sh
make deploy SHA=<previous-sha>          # asks GitHub to run the deploy workflow, without migrations
make prod-deploy SHA=<previous-sha>     # the same over SSM with your login: works from any branch
```

`make deploy` needs the workflow to exist on the default branch (GitHub lists a workflow only
then), which is true after the sprint is merged; `make prod-deploy` sends the same SSM document
directly and is the way before that and the way if GitHub is down. Without migrations, because one
release of schema compatibility (NFR-10.4, CONTRIBUTING.md) means the previous release runs on the
new schema, so a rollback never needs a schema downgrade. Roll forward the same way:
`make deploy SHA=<newer-sha>` (the migration already ran), or add `MIGRATE=true` for a release
that has one that hasn't.

**Check:** `curl -s https://2nd-mind.<domain>/v1/meta` shows the SHA you asked for under `version`.

**Tried** (`deploy.sh <sha> --no-migrate` on the sandbox, 2026-09-30):

```text
==> 16:45:44 release b5e88c310ab0 (previous: b5e88c310ab0); migrate=0
==> 16:45:45 up
==> 16:45:46 release b5e88c310ab0 is serving
```

The rollback through GitHub and SSM is yours to try (guide Part 3, exercise 2).

## 7. Restore the database

**When:** data was lost or damaged, or to check that backups work. There are two layers:

- **Daily snapshots** of the data volume (7 kept): Data Lifecycle Manager. They bring back the whole
  disk (Postgres, Redis, the edge's certificates).
- **A nightly `pg_dump`** (02:30 IST) kept on the host for two days and in the backup bucket for 30:
  `s3://secondmind-backups-<account-id>/dumps/<year>/<month>/secondmind-<timestamp>.dump`. It's a
  PostgreSQL custom-format dump.

**Restore a dump into the local stack** (to look at production's data, or to prove the backup):

```sh
aws s3 cp s3://secondmind-backups-<account-id>/dumps/<year>/<month>/<file>.dump ./last.dump --profile secondmind
make up
make restore-local DUMP=./last.dump
```

`make restore-local` replaces the local database (`scripts/restore-local.sh`): it stops the api and
worker, drops and recreates the database, creates the app's database roles, loads the dump with
`pg_restore --no-owner`, runs the migrations to this checkout's head, and starts the api and worker
again.

**Tried** (a dump of the rehearsal database, loaded into an isolated local stack, 2026-09-30):

```text
==> stopping the api and worker
==> replacing the database
==> the app's database roles
bootstrap: role 'secondmind_app' ready (member of secondmind_rw)
==> restoring rehearsal.dump
==> migrating to this checkout's head
restored.
rows before (users|turns|memory_items|usage_ledger)  20|30|25|163
rows after                                            20|30|25|163
```

A user from the dump signed in on the restored stack and saw their workspace.

**Restore production itself** (the disk is gone, or the database is corrupt): from a snapshot,
create a volume in the same availability zone, stop the instance, swap it in for the data volume
(or restore the dump into the running Postgres: `pg_restore --clean --if-exists --no-owner` inside the
postgres container), start it, and run `make smoke-prod`. Practise the dump half at least once
(guide Part 3, exercise 4). The point-in-time restore of S8's load-test sprint is out of scope here:
the most a failure loses is a day.

## 8. Alerts

*Stub. S8: the `spend.cap_warning` log event, provider error rates and readiness failures become
alarms.*

## 9. The host

The everyday operations on the production host. All of them need your AWS login, and none is run by
the session.

**Get a shell:** `aws ssm start-session --target <instance-id> --profile secondmind` (the id is
`scripts/tf.sh platform output -raw instance_id`). No SSH, no open port.

**Run one thing without a shell:** `scripts/prod-ssm.sh shell '<command>'`;
`scripts/prod-ssm.sh admin spend` (the admin CLI); `make prod-admin CMD="kill-switch status"`.

**Stop and start** (before launch, to save credit): `make host-stop`, `make host-start`,
`make host-status`. Stopped, the instance's $12.3 a month stops; the Elastic IP and the disks
(about $5.60 a month) keep drawing. After a start the site returns by itself: the data volume
mounts before Docker starts, and every container is `restart: unless-stopped`.

**Patching.** `unattended-upgrades` installs security updates daily. A systemd timer
(`secondmind-reboot.timer`, Sunday 03:30 IST) reboots only if `/var/run/reboot-required` exists.
To look: `scripts/prod-ssm.sh shell 'apt list --upgradable 2>/dev/null | head; cat /var/run/reboot-required || echo none'`.
To reboot now: `scripts/prod-ssm.sh shell 'shutdown -r +1'`, then `scripts/check-deployed.sh https://2nd-mind.<domain>`.

**Logs.** Containers log JSON to CloudWatch, group `/secondmind/prod`, kept 7 days, and never contain
message content. One turn's lines: Logs Insights, `fields @timestamp, @message | filter @message like /<turn-id>/ | sort @timestamp asc`.
On the host, `docker logs secondmind-api-1 --tail 100` also works (the awslogs driver keeps a local copy).

**Memory.** `free -h` and `docker stats --no-stream`. The stack is measured at about 0.6 GB in use
(peak 611 MiB on the `@prodlike` subset) and its limits add up to about 1.6 GB; the 2 GB swap file is
a cushion, not working memory.

**Timers.** `systemctl list-timers "secondmind-*"` shows the nightly dump and the reboot window;
`sudo /opt/secondmind/infra/host/backup.sh` takes a dump now.
