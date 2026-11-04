COMPOSE := docker compose
DEV := $(COMPOSE) --profile dev run --rm dev
UV_IMAGE := ghcr.io/astral-sh/uv:0.13.7-python3.14-alpine
export UID := $(shell id -u)
export GID := $(shell id -g)

.DEFAULT_GOAL := help
.PHONY: help up down logs migrate seed user dev test test-fast lint format lock audit build

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

lock: ## Update uv.lock and package-lock.json
	docker run --rm -u $(UID):$(GID) -e HOME=/tmp -v "$(CURDIR)":/app -w /app $(UV_IMAGE) uv lock
	npm install --package-lock-only --no-audit --no-fund

build: ## Build the production image
	docker build --target runtime -t hourplan:local .
