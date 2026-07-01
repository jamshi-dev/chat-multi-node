.DEFAULT_GOAL := help
.PHONY: help install lock sync lint fmt typecheck test check up down logs ps \
        migrate revision dev clean demo

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install all deps (incl. dev)
	uv sync

lock: ## Update the lockfile
	uv lock

lint: ## Lint with ruff
	uv run ruff check app tests

fmt: ## Auto-format + autofix with ruff
	uv run ruff format app tests
	uv run ruff check --fix app tests

typecheck: ## Type-check with ty
	uv run ty check app

test: ## Run the test suite
	uv run pytest -q

check: lint typecheck test ## Lint + typecheck + test (CI gate)

up: ## Build and start the full stack (Postgres, Redis, 2 API replicas)
	docker compose up --build

down: ## Stop the stack and remove volumes
	docker compose down -v

logs: ## Tail API logs
	docker compose logs -f api1 api2

ps: ## Show running containers
	docker compose ps

migrate: ## Apply migrations against the running stack
	docker compose run --rm migrate

revision: ## Autogenerate a migration: make revision m="message"
	uv run alembic revision --autogenerate -m "$(m)"

dev: ## Run the API locally with autoreload (needs local Postgres+Redis)
	uv run uvicorn app.main:app --reload --port 8000

demo: ## Run the scripted end-to-end demo against the running stack
	uv run python scripts/demo.py

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache .ty_cache **/__pycache__
