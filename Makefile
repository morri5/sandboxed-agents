.PHONY: lint test test-integration test-chaos spike check-names db-up migrate api

PY_SOURCES := spikes src tests

lint:
	uv run ruff check $(PY_SOURCES)
	uv run ruff format --check $(PY_SOURCES)
	cd spikes && uv run mypy .
	uv run mypy src tests

# Unit tests need Postgres (they use their own lab_test database); no VMs.
test: db-up
	uv run pytest -m "not integration"

# Real microVMs (needs network for the image pull and the git clone).
test-integration: db-up
	uv run pytest -m "integration and not chaos"

# Kills the API with SIGKILL mid-run and restarts it; prints the recovery times.
test-chaos: db-up
	uv run pytest -m chaos -s

# Each spike prints its measurements and writes spikes/out/<spike>.json.
# The network spike needs DEEPSEEK_API_KEY in the environment.
spike:
	cd spikes && uv run python sandbox_spike.py
	cd spikes && uv run python network_spike.py
	cd spikes && uv run python reattach_spike.py

check-names:
	./scripts/check_names.sh

db-up:
	docker compose up -d --wait

migrate: db-up
	uv run python -m lab.db

api: migrate
	uv run uvicorn lab.api:app
