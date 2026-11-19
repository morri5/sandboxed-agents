.PHONY: lint test test-integration test-chaos spike spike-factory check-names db-up migrate api \
	claude-sdk grader-deps eval-check eval web-test demo

PY_SOURCES := spikes src tests scripts

lint:
	uv run ruff check $(PY_SOURCES)
	uv run ruff format --check $(PY_SOURCES)
	cd spikes && uv run mypy .
	uv run mypy src tests scripts

# Unit tests need Postgres (they use their own lab_test database); no VMs.
test: db-up
	uv run pytest -m "not integration"

# Real microVMs (needs network for the image pull and the git clone).
test-integration: db-up claude-sdk
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

# research_codebase from LAB_FACTORY_COMMANDS_DIR, with the subagents in LAB_FACTORY_AGENTS_DIR,
# on fixtures/factory-repo through the claude_factory harness (DeepSeek, a few cents). Compares
# the reported cost with the DeepSeek balance. Starts its own API: stop `make api` first.
spike-factory: migrate claude-sdk
	cd spikes && uv run python factory_spike.py

check-names:
	./scripts/check_names.sh

db-up:
	docker compose up -d --wait

migrate: db-up
	uv run python -m lab.db

api: migrate
	uv run uvicorn lab.api:app

# One chat on fixtures/calc-repo, streamed to the terminal, then closed. Starts its own API
# on a free port: stop `make api` first. DEMO_HARNESS=deepagents_ollama needs Ollama.
DEMO_HARNESS ?= scripted
demo: migrate
	uv run python scripts/demo.py $(DEMO_HARNESS)

# The Claude Agent SDK (and its CLI) for Linux arm64, mounted into claude_agent VMs.
claude-sdk:
	uv run python -c 'from lab.harnesses.claude_agent import install_sdk; install_sdk()'

# pytest for Linux arm64, mounted read-only into grader VMs (which have no network).
grader-deps:
	uv run python -c 'from lab.evals.grader import install_pytest; install_pytest()'

# The grader's adversarial suite against every task (real VMs, no model).
eval-check: grader-deps
	uv run python -m lab.evals.check

# Every task EVAL_REPEATS times per harness through the API, graded in fresh VMs.
# Writes evals/results/<date>.jsonl and .md. Stop `make api` first.
EVAL_HARNESS ?= deepagents_ollama,claude_agent
EVAL_REPEATS ?= 3
# EVAL_TASKS: comma-separated task names or shell patterns (hard-*); default all.
EVAL_TASKS ?=
eval: migrate claude-sdk eval-check
	uv run python -m lab.evals.run --harness $(EVAL_HARNESS) --repeats $(EVAL_REPEATS) \
		$(if $(EVAL_TASKS),--tasks '$(EVAL_TASKS)')

# Playwright: two tabs on one session (scripted harness, real VM). Starts its own API on
# port 8765 with the lab_test database, and the page on port 3100.
web-test: db-up
	npm --prefix web run test:e2e
