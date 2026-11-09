COMPOSE := docker compose
DEV := $(COMPOSE) --profile dev run --rm dev
UV_IMAGE := ghcr.io/astral-sh/uv:0.13.7-python3.14-alpine
export UID := $(shell id -u)
export GID := $(shell id -g)

.DEFAULT_GOAL := help
.PHONY: help up down logs migrate seed user demo dev test test-fast lint format lock audit build e2e css voice-models voice-up backup restore

help: ## Show available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

up: ## Build and start the app (http://127.0.0.1:8000)
	$(COMPOSE) up -d --build --wait db web
	$(MAKE) migrate

down: ## Stop the containers
	$(COMPOSE) --profile dev down

logs: ## Follow the app logs
	$(COMPOSE) logs -f web

migrate: ## Apply database migrations
	$(COMPOSE) run --rm web alembic upgrade head

seed: ## Load the initial catalogs, phase templates and policies (idempotent)
	$(COMPOSE) run --rm web flask seed

demo: ## Load fictitious demo data (empty database only)
	$(COMPOSE) run --rm web sh -c "flask seed && flask demo-data"

user: ## Create a user account (interactive)
	$(COMPOSE) run --rm web flask create-user

dev: ## Development server with reload (http://127.0.0.1:8001) and CSS watcher
	$(COMPOSE) --profile dev up --build dev assets

test: ## Full test suite with coverage
	$(DEV) pytest -n auto --cov --cov-report=term

test-fast: ## Tests matching K (make test-fast K=login)
	$(DEV) pytest -q $(if $(K),-k "$(K)")

lint: ## Ruff, formatting, mypy and architecture contracts
	$(DEV) sh -c "ruff check . && ruff format --check . && mypy && lint-imports"

format: ## Format and auto-fix
	$(DEV) sh -c "ruff format . && ruff check --fix ."

audit: ## Known vulnerabilities in dependencies
	$(DEV) sh -c "uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/req.txt && pip-audit --no-deps --disable-pip -r /tmp/req.txt"

css: ## Rebuild the compiled CSS and vendored scripts (needed after template changes)
	docker run --rm -u $(UID):$(GID) -e HOME=/tmp -v "$(CURDIR)":/app -w /app node:24.22.2-alpine3.24 sh -c "npm ci --no-audit --no-fund && npm run build"

voice-models: ## Download a Whisper model into the volume (make voice-models M=small|base|medium|turbo)
	$(COMPOSE) --profile voice-models run --rm voice-models flask voice-fetch-model $(or $(M),small)

voice-up: ## Start dictation: Whisper + worker (add LLM=1 for the local LLM)
	$(COMPOSE) up -d --build --wait db web
	$(MAKE) migrate
	$(COMPOSE) --profile voice $(if $(LLM),--profile voice-llm) up -d --build --wait whisper voice-worker $(if $(LLM),llm)

e2e: css ## Browser tests (Playwright in Docker) on a throwaway database; also refreshes docs/capturas
	$(COMPOSE) up -d --wait db
	$(COMPOSE) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS hourplan_e2e WITH (FORCE)" -c "CREATE DATABASE hourplan_e2e"'
	mkdir -p docs/capturas
	$(COMPOSE) --profile e2e run --rm e2e; status=$$?; $(COMPOSE) --profile e2e down e2e-app; exit $$status

lock: ## Update uv.lock and package-lock.json
	docker run --rm -u $(UID):$(GID) -e HOME=/tmp -v "$(CURDIR)":/app -w /app $(UV_IMAGE) uv lock
	npm install --package-lock-only --no-audit --no-fund

backup: ## Dump the database to backups/ (BACKUP_KEEP=14 newest files are kept)
	@mkdir -p backups
	@f=backups/hourplan-$$(date +%Y%m%d-%H%M%S).dump; \
	$(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc' > $$f && echo "Backup: $$f"; \
	ls -1t backups/*.dump | tail -n +$$(( $${BACKUP_KEEP:-14} + 1 )) | xargs -r rm --

restore: ## Restore a dump into the database (make restore F=backups/file.dump) - replaces data
	@test -f "$(F)" || { echo "Usage: make restore F=backups/file.dump"; exit 1; }
	$(COMPOSE) stop web
	$(COMPOSE) exec -T db sh -c 'pg_restore -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --clean --if-exists --no-owner' < $(F)
	$(COMPOSE) up -d --wait web

build: ## Build the production image
	docker build --target runtime -t hourplan:local .
