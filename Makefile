.PHONY: help setup up down nuke migrate psql doctor lint fmt test test-all graph run clean
.DEFAULT_GOAL := help

UV := uv

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup:  ## Pin python 3.12, install all extras + dev deps
	$(UV) python pin 3.12
	$(UV) sync --all-extras

setup-browser: setup  ## Additionally install the Playwright chromium build (M2/M5/M6)
	$(UV) run playwright install chromium

up:  ## Start Postgres+pgvector (host port 5433) and wait for health
	docker compose up -d --wait

down:  ## Stop the database container
	docker compose down

nuke:  ## Stop AND drop the volume (re-runs ops/initdb on next `up`)
	docker compose down -v

migrate:  ## Apply Alembic migrations to head
	$(UV) run alembic upgrade head

sql:  ## Print the DDL Alembic would apply, without touching the database
	$(UV) run alembic upgrade head --sql

psql:  ## Open a psql shell on the app database
	docker compose exec db psql -U jobagent -d jobagent

doctor:  ## Report every unmet dependency (docker, soffice, playwright, auth, DSN)
	$(UV) run jobagent doctor

lint:  ## ruff check + format check + mypy
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy jobagent

fmt:  ## Autofix lint + format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test:  ## Unit tests only -- green with the Docker daemon down
	$(UV) run pytest -m "not pg"

test-all:  ## Unit + integration; needs `make up && make migrate`
	$(UV) run pytest

graph:  ## Render the whole graph as Mermaid (no database required)
	$(UV) run jobagent graph --mermaid --xray

demo:  ## M1 end-to-end: seed 4 postings, dedupe, persist, export CSV. No network.
	$(UV) run jobagent tracker --seed fixtures/seed_jobs.json

run:  ## Full pipeline. DRY-RUN BY DEFAULT -- see safety.py for the two-key rule.
      ## In M1 this stops at discovery.plan_queries (M2 stub); use `make demo` meanwhile.
	$(UV) run jobagent run

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
