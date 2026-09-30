SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE     ?= docker compose
BACKEND_RUN := cd backend && uv run --frozen
# Live targets only: .env exported for that one command, and MODEL_PROVIDER_MODE=live.
LIVE_RUN    := cd backend && ../scripts/live-env.sh uv run --frozen
WEB_RUN     := cd frontend && npm run --silent
# Eval selection, e.g. `make eval-recall-live ROUTING=economy-b CASES=@probe`.
EVAL_ARGS    = $(if $(ROUTING),--routing $(ROUTING)) $(if $(CASES),--cases '$(CASES)') $(ARGS)
WEB_URL     ?= http://localhost:$${WEB_PORT:-8080}

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup
.PHONY: install
install: ## Install backend, frontend and repo tooling (git hooks)
	npm install --no-audit --no-fund
	cd backend && uv sync --frozen
	cd frontend && npm ci --no-audit --no-fund

# ------------------------------------------------------------------ local stack
.PHONY: up
up: ## Build and start the whole stack, wait until healthy, print URLs
	$(COMPOSE) build api web
	$(COMPOSE) up -d --wait
	@scripts/print-urls.sh

.PHONY: up-live
up-live: ## The stack on real providers (keys from .env; a missing key refuses to start)
	$(COMPOSE) build api web
	MODEL_PROVIDER_MODE=live $(COMPOSE) up -d --wait
	@scripts/print-urls.sh

.PHONY: down
down: ## Stop the stack (keeps volumes; `make clean-volumes` drops them)
	$(COMPOSE) down

# The production-shaped rehearsal (R.13, S4.4): its own project and volumes, the production compose
# file, images tagged by SHA. `make up-prodlike` builds them locally; production pulls the same
# images from GHCR (IMAGE_REGISTRY).
PRODLIKE_TAG ?= $(shell git rev-parse --short=12 HEAD)
PRODLIKE     := IMAGE_TAG=$(PRODLIKE_TAG) $(COMPOSE) -p secondmind-prodlike \
	-f compose.prodlike.yaml --env-file .prodlike.env
PRODLIKE_URL ?= https://localhost:8443

# Operator commands (kill-switch, set-tier, spend-now) talk to the plain stack, or with STACK=prodlike
# to the rehearsal stack.
STACKCMD = $(if $(filter prodlike,$(STACK)),$(PRODLIKE),$(COMPOSE))

.PHONY: prodlike-env
prodlike-env: ## (Re)write .prodlike.env: the rehearsal's generated secrets and the provider keys from .env
	@scripts/prodlike-env.sh

.PHONY: up-prodlike
up-prodlike: prodlike-env ## The production stack on this machine: live models, access code, TLS from a local CA (stop `make up` first)
	# Built once (migrate, api and worker share the image; three builds at once race), tagged by SHA.
	IMAGE_TAG=$(PRODLIKE_TAG) APP_VERSION=$(PRODLIKE_TAG) $(COMPOSE) build api web
	$(PRODLIKE) up -d --wait
	@echo "    Rehearsal   $(PRODLIKE_URL)  (Caddy's local certificate: trust it once, or click through)"
	@echo "    Access code  $$(sed -n 's/^ACCESS_CODE=//p' .prodlike.env)"

.PHONY: e2e-prodlike
e2e-prodlike: ## The @prodlike E2E subset on the rehearsal stack, real models (costs cents; run `make up-prodlike` first)
	cd frontend && IMAGE_TAG=$(PRODLIKE_TAG) E2E_BASE_URL=$(PRODLIKE_URL) E2E_IGNORE_HTTPS_ERRORS=1 \
		E2E_ACCESS_CODE=$$(sed -n 's/^ACCESS_CODE=//p' ../.prodlike.env) \
		E2E_COMPOSE="-p secondmind-prodlike -f ../compose.prodlike.yaml --env-file ../.prodlike.env" \
		npx playwright test --grep @prodlike --project=desktop --project=spend --no-deps

.PHONY: measure-prodlike
measure-prodlike: ## Peak memory of the rehearsal stack while the @prodlike subset runs (prints each container against its limit)
	scripts/measure_memory.py -- $(MAKE) e2e-prodlike

.PHONY: down-prodlike
down-prodlike: ## Stop the rehearsal stack (keeps its volumes)
	$(PRODLIKE) down

.PHONY: clean-prodlike
clean-prodlike: ## Stop the rehearsal stack and delete its volumes (the database, Redis, the local CA)
	$(PRODLIKE) down -v

.PHONY: clean-volumes
clean-volumes: ## Stop the stack and delete its data volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail stack logs
	$(COMPOSE) logs -f --tail=100

.PHONY: migrate
migrate: ## Apply database migrations (against the compose database)
	$(COMPOSE) run --rm --build migrate

# ------------------------------------------------------------------ quality gates
.PHONY: check
check: commits lint typecheck imports openapi-check test test-int web-check infra-check secrets audit scan-images ## Everything CI runs, except E2E

.PHONY: commits
commits: ## Commit messages not yet on origin/main follow the standard; authors too if COMMIT_AUTHORS is set
	@if git rev-parse --verify --quiet origin/main >/dev/null; then \
		npx --no -- commitlint --from origin/main --to HEAD; \
		if [ -n "$${COMMIT_AUTHORS:-}" ]; then scripts/check-commit-authors.sh origin/main HEAD; fi; \
	else npx --no -- commitlint --last; fi

.PHONY: lint
lint: ## Ruff lint + format check (backend)
	$(BACKEND_RUN) ruff check .
	$(BACKEND_RUN) ruff format --check .

.PHONY: typecheck
typecheck: ## mypy --strict (backend)
	$(BACKEND_RUN) mypy

.PHONY: imports
imports: ## Import-boundary contracts (import-linter)
	$(BACKEND_RUN) env PYTHONPATH=tools lint-imports

.PHONY: openapi-check
openapi-check: ## Fail if backend/openapi.json is out of date with the code
	$(BACKEND_RUN) python -m secondmind.api.openapi --check openapi.json

.PHONY: test
test: ## Backend unit tests
	$(BACKEND_RUN) pytest

.PHONY: test-int
test-int: ## Backend integration tests (testcontainers Postgres + Redis; needs Docker; no live calls)
	$(BACKEND_RUN) pytest -m "integration and not live"

.PHONY: test-live
test-live: ## Provider contract tests on real APIs, economy models (keys from .env; costs cents)
	$(LIVE_RUN) env LIVE_ANTHROPIC_MODEL=$${LIVE_ANTHROPIC_MODEL:-claude-haiku-4-5} \
		LIVE_OPENAI_MODEL=$${LIVE_OPENAI_MODEL:-gpt-6-luna} \
		pytest -m live tests/unit/providers/test_contract.py -q

.PHONY: live-check
live-check: ## One tiny request per routed and picker model; masked keys, prices, prompt lock
	$(LIVE_RUN) python -m secondmind.evals live-check

.PHONY: live-spend
live-spend: ## What live runs have spent so far, against LIVE_TOTAL_BUDGET_USD
	$(BACKEND_RUN) python -m secondmind.evals spend

.PHONY: eval-intent
eval-intent: ## Intent accuracy on the labelled messages, fake provider (run file, gitignored)
	$(BACKEND_RUN) python -m secondmind.evals intent $(EVAL_ARGS)

.PHONY: eval-intent-live
eval-intent-live: ## Intent accuracy on real providers (keys from .env; budgeted; costs money)
	$(LIVE_RUN) python -m secondmind.evals intent --live $(EVAL_ARGS)

.PHONY: eval-ingest
eval-ingest: ## Ingestion golden cases on replayed model outputs, with the score table
	$(BACKEND_RUN) python -m secondmind.evals ingest $(EVAL_ARGS)

.PHONY: eval-ingest-live
eval-ingest-live: ## Ingestion golden cases on real providers (keys from .env; budgeted; costs money)
	$(LIVE_RUN) python -m secondmind.evals ingest --live $(EVAL_ARGS)

.PHONY: record-replays
record-replays: ## Rebuild golden model blocks from a live run: SUITE=ingest|recall FROM=<run id> [CASES=…] [WRITE=1]
	$(BACKEND_RUN) python -m secondmind.evals record-replays $(SUITE) --from $(FROM) $(if $(CASES),--cases $(CASES)) $(if $(WRITE),--write,--diff)

.PHONY: eval-recall
eval-recall: ## Recall golden cases on replayed plans and rerank scores (throwaway Postgres)
	$(BACKEND_RUN) python -m secondmind.evals recall $(EVAL_ARGS)

.PHONY: eval-recall-live
eval-recall-live: ## Recall golden cases on real providers (keys from .env; budgeted; costs money)
	$(LIVE_RUN) python -m secondmind.evals recall --live $(EVAL_ARGS)

.PHONY: eval-dry
eval-dry: ## Any suite as a dry run with its estimated live cost: make eval-dry SUITE=recall ...
	$(BACKEND_RUN) python -m secondmind.evals $(SUITE) --dry-run $(EVAL_ARGS)

.PHONY: eval-report
eval-report: ## Print a stamped run, or two side by side: make eval-report A=<run> B=<run>
	$(BACKEND_RUN) python -m secondmind.evals report $(A) $(B)

.PHONY: bench-vectors
bench-vectors: ## Vector storage bench under RLS (throwaway pgvector; clustered random vectors; free)
	$(BACKEND_RUN) env -u OPENAI_API_KEY python tools/bench_vectors.py --random

.PHONY: bench-vectors-live
bench-vectors-live: ## The vector bench on real embeddings (key from .env; ~$0.09; on the spend total)
	$(LIVE_RUN) python tools/bench_vectors.py

.PHONY: smoke-live
smoke-live: ## Drive the running stack through the S3 demo and S2 examples on real models (SMOKE_ACCESS_CODE; costs money)
	$(LIVE_RUN) python tools/smoke_live.py $(ARGS)

.PHONY: seed-dev
seed-dev: ## Reset the dev user's workspace and seed the synthetic recall fixture (compose stack)
	$(COMPOSE) exec api python -m secondmind.evals seed-dev --web-url $(WEB_URL)

# Spend safety (R.10, ADR-0032): operate the running stack without a deploy.
ADMIN := python -m secondmind.api.admin

.PHONY: kill-switch on off
kill-switch: ## make kill-switch on|off (or STATE=status): stop or resume every model call, no restart
	@$(STACKCMD) exec -T api $(ADMIN) kill-switch $(or $(STATE),$(filter on off,$(MAKECMDGOALS)),status)
on off: ; @:

.PHONY: set-tier
set-tier: ## make set-tier EMAIL=… TIER=guest|standard|premium: change a person's tier (audited)
	@test -n "$(EMAIL)" -a -n "$(TIER)" || { echo "usage: make set-tier EMAIL=a@b.c TIER=premium"; exit 2; }
	@$(STACKCMD) exec -T api $(ADMIN) set-tier "$(EMAIL)" "$(TIER)"

.PHONY: spend-now
spend-now: ## Today's, this month's and each provider's spend counters (the running stack)
	@$(STACKCMD) exec -T api $(ADMIN) spend

.PHONY: backfill-conversation
backfill-conversation: ## Index past chat turns for "what did you tell me" questions (runs in the worker)
	$(COMPOSE) exec worker python -m secondmind.jobs.adapters.enqueue backfill_conversation

.PHONY: web-check
web-check: ## Frontend lint, typecheck, build and the design-system check (tokens, contrast, budget)
	$(WEB_RUN) lint
	$(WEB_RUN) typecheck
	$(WEB_RUN) build
	$(WEB_RUN) check:design

.PHONY: secrets
secrets: ## gitleaks + hygiene hooks over the whole repo
	cd backend && uv run --frozen pre-commit run --all-files --show-diff-on-failure

.PHONY: audit
audit: ## Dependency vulnerability audit (pip-audit + npm audit)
	cd backend && uv export --frozen --no-dev --no-emit-project --format requirements-txt \
		> ../.tmp-requirements.txt && uv run --frozen pip-audit --strict --disable-pip \
		--requirement ../.tmp-requirements.txt; status=$$?; rm -f ../.tmp-requirements.txt; exit $$status
	cd frontend && npm audit --audit-level=high

.PHONY: images
images: ## Build the api/worker and web images
	$(COMPOSE) build api web

.PHONY: scan-images
scan-images: images ## Trivy scan: fail on fixable CRITICAL vulnerabilities
	scripts/trivy-scan.sh secondmind-api:$${IMAGE_TAG:-local} secondmind-web:$${IMAGE_TAG:-local}

.PHONY: e2e
e2e: ## Playwright smoke test against the compose stack in fake-provider mode
	$(COMPOSE) build api web
	MODEL_PROVIDER_MODE=fake $(COMPOSE) up -d --wait
	cd frontend && E2E_BASE_URL=$(WEB_URL) npx playwright test

# ------------------------------------------------------------------ infrastructure (ADR-0035)
# Terraform, tflint and trivy run in containers: nothing to install. The checks are offline (the
# AWS provider is mocked in the tests), so they need no account. plan/apply/init need your AWS
# profile (AWS_PROFILE) and are yours to run (guide Part 2).
TF_IMAGE      ?= hashicorp/terraform:1.16.4
TFLINT_IMAGE  ?= ghcr.io/terraform-linters/tflint:v0.64.0
TRIVY_IMAGE   ?= aquasec/trivy:0.74.0
AWS_PROFILE   ?= secondmind
ROOT          ?= platform

.PHONY: infra-check
infra-check: tf-check tf-lint tf-scan ## Terraform: format, validate, offline tests, tflint, trivy config (containers, no account)

.PHONY: tf-check
tf-check: ## terraform fmt -check, validate and the mocked-provider tests, every root and module
	@mkdir -p $(HOME)/.cache/terraform-plugins
	docker run --rm --entrypoint sh -v $(CURDIR)/infra:/infra \
		-v $(HOME)/.cache/terraform-plugins:/plugin-cache -e TF_PLUGIN_CACHE_DIR=/plugin-cache \
		$(TF_IMAGE) /infra/terraform/check.sh

.PHONY: tf-lint
tf-lint: ## tflint (with the AWS ruleset) over every root and module
	docker run --rm --entrypoint sh -v $(CURDIR)/infra/terraform:/data -w /data \
		-e GITHUB_TOKEN $(TFLINT_IMAGE) -c "tflint --init && tflint --recursive"

.PHONY: tf-scan
tf-scan: ## trivy config over the Terraform (fails on HIGH and CRITICAL; accepted ones are in .trivyignore)
	docker run --rm -v $(CURDIR)/infra/terraform:/src -v $(HOME)/.cache/trivy:/root/.cache/trivy \
		$(TRIVY_IMAGE) config --exit-code 1 --severity HIGH,CRITICAL --ignorefile /src/.trivyignore /src

.PHONY: tf-bootstrap
tf-bootstrap: ## Create the Terraform state bucket (yours to run: AWS_PROFILE=...)
	AWS_PROFILE=$(AWS_PROFILE) scripts/tf-bootstrap.sh

.PHONY: tf-init tf-plan tf-apply tf-output
tf-init: ## terraform init for ROOT=platform|app (yours to run)
	AWS_PROFILE=$(AWS_PROFILE) scripts/tf.sh $(ROOT) init
tf-plan: ## terraform plan for ROOT=platform|app (yours to run)
	AWS_PROFILE=$(AWS_PROFILE) scripts/tf.sh $(ROOT) plan
tf-apply: ## terraform apply for ROOT=platform|app (yours to run)
	AWS_PROFILE=$(AWS_PROFILE) scripts/tf.sh $(ROOT) apply
tf-output: ## terraform output for ROOT=platform|app
	AWS_PROFILE=$(AWS_PROFILE) scripts/tf.sh $(ROOT) output

.PHONY: prod-secrets
prod-secrets: ## Write .env.prod to SSM Parameter Store (PROD_SECRETS_DRY_RUN=1 checks only; yours to run)
	AWS_PROFILE=$(AWS_PROFILE) scripts/prod-secrets.sh

.PHONY: host-stop host-start host-status
host-stop: ## Stop the production instance to save credit (before launch only; yours to run)
	AWS_PROFILE=$(AWS_PROFILE) scripts/host.sh stop
host-start: ## Start it again; the site comes back by itself
	AWS_PROFILE=$(AWS_PROFILE) scripts/host.sh start
host-status: ## Is the production instance running?
	AWS_PROFILE=$(AWS_PROFILE) scripts/host.sh status

.PHONY: deploy
deploy: ## Deploy through GitHub: make deploy [SHA=<sha>: a rollback, no migrations; MIGRATE=true|false] [REF=<branch>]
	scripts/deploy.sh "$(SHA)" "$(MIGRATE)" "$(REF)"

.PHONY: prod-deploy
prod-deploy: ## Deploy SHA=<sha> straight over SSM, without GitHub (a rollback before the workflow is on main; yours to run)
	@test -n "$(SHA)" || { echo "usage: make prod-deploy SHA=<sha> [MIGRATE=false]"; exit 2; }
	AWS_PROFILE=$(AWS_PROFILE) scripts/prod-ssm.sh deploy "$(SHA)" $(if $(filter false,$(MIGRATE)),no-migrate,migrate)

.PHONY: prod-admin
prod-admin: ## The admin CLI in production's api container over SSM: make prod-admin CMD="kill-switch on" (yours to run)
	@test -n "$(CMD)" || { echo 'usage: make prod-admin CMD="kill-switch on|off|status | spend | set-tier <email> <tier>"'; exit 2; }
	AWS_PROFILE=$(AWS_PROFILE) scripts/prod-ssm.sh admin $(CMD)

.PHONY: smoke-prod
smoke-prod: ## Readiness, headers, then the @prodlike E2E subset on real models against PROD_URL; prints cost and a run id (yours to run)
	AWS_PROFILE=$(AWS_PROFILE) scripts/smoke-prod.sh

.PHONY: restore-local
restore-local: ## Load a nightly dump into the local stack: make restore-local DUMP=<file> (replaces its database; runbook section 7)
	@test -n "$(DUMP)" || { echo "usage: make restore-local DUMP=<file>"; exit 2; }
	scripts/restore-local.sh "$(DUMP)"

# ------------------------------------------------------------------ codegen and formatting
.PHONY: gen-client
gen-client: ## Regenerate backend/openapi.json and the TS client from it
	$(BACKEND_RUN) python -m secondmind.api.openapi --write openapi.json
	$(WEB_RUN) gen-client

.PHONY: fmt
fmt: ## Auto-format and auto-fix (backend + frontend)
	$(BACKEND_RUN) ruff check --fix .
	$(BACKEND_RUN) ruff format .
	cd frontend && npx eslint . --fix
