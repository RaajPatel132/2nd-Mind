SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE     ?= docker compose
BACKEND_RUN := cd backend && uv run --frozen
WEB_RUN     := cd frontend && npm run --silent
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
	$(COMPOSE) up -d --build --wait
	@scripts/print-urls.sh

.PHONY: down
down: ## Stop the stack (keeps volumes; `make clean-volumes` drops them)
	$(COMPOSE) down

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
check: commits lint typecheck imports openapi-check test test-int web-check secrets audit scan-images ## Everything CI runs, except E2E

.PHONY: commits
commits: ## Commit messages not yet on origin/main follow the standard (commitlint)
	@if git rev-parse --verify --quiet origin/main >/dev/null; then \
		npx --no -- commitlint --from origin/main --to HEAD; \
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
test-int: ## Backend integration tests (testcontainers Postgres + Redis; needs Docker)
	$(BACKEND_RUN) pytest -m integration

.PHONY: test-live
test-live: ## Provider contract tests against real APIs (needs keys)
	$(BACKEND_RUN) pytest -m live

.PHONY: eval-ingest
eval-ingest: ## Ingestion golden cases on replayed model outputs, with the score table
	$(BACKEND_RUN) python -m secondmind.evals ingest

.PHONY: eval-ingest-live
eval-ingest-live: ## Ingestion golden cases on the configured real providers (needs keys; costs money)
	$(BACKEND_RUN) python -m secondmind.evals ingest --live

.PHONY: web-check
web-check: ## Frontend lint, typecheck and build
	$(WEB_RUN) lint
	$(WEB_RUN) typecheck
	$(WEB_RUN) build

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
	scripts/trivy-scan.sh secondmind-api:local secondmind-web:local

.PHONY: e2e
e2e: ## Playwright smoke test against the compose stack in fake-provider mode
	MODEL_PROVIDER_MODE=fake $(COMPOSE) up -d --build --wait
	cd frontend && E2E_BASE_URL=$(WEB_URL) npx playwright test

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
