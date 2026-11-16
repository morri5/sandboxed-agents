# sandboxed-agents-lab

A small lab for coding agents that run in per-chat microVMs ([microsandbox](https://github.com/superradcompany/microsandbox)), behind a session layer whose single source of truth is an append-only event log in PostgreSQL.

Work in progress. The first step was a set of spikes that check microsandbox on Apple Silicon and measure what a VM costs: see [docs/spike-results.md](docs/spike-results.md). The session layer runs a chat end to end: a session gets its own VM with a copy of the repo, an agent works on it, and every durable event lands in the log and streams to clients. Three harnesses exist: two real coding agents, placed where their vendors designed them to run (deepagents on a local Ollama model, loop on the host and tools in a VM without network; the Claude Agent SDK inside the VM, with DeepSeek as its brain and the API key kept outside the VM), and a scripted fake agent that runs inside the VM, for tests. A chat can switch harness between messages. A session survives a `kill -9` of the API: on restart every open session is reconciled with its VM and with the event file the in-VM runner keeps, and a chat can be forked at any event between runs.

## Architecture

### How it works today

```mermaid
flowchart LR
    CLI[CLI: lab new / say / tail] -->|HTTP POST| API
    CLI <-->|WebSocket stream| API
    subgraph HOST["Mac (host)"]
        API[FastAPI] -->|queue| SR[Session runner<br/>only writer per session]
        SR -->|append event| PG[(PostgreSQL<br/>append-only event log)]
        PG -->|"pg_notify {session_id, seq}"| FO[Fan-out<br/>one LISTEN connection]
        FO --> API
        SR -.->|agent_delta, memory only| FO
        SR --> DA[deepagents loop<br/>harness A, in the API process]
        DA -->|model calls| OL[Ollama on 127.0.0.1<br/>Gemma 4]
        PX[microsandbox secret proxy<br/>holds the real DeepSeek key]
    end
    subgraph VMA["microVM for harness A (detached, no network)"]
        REPOA["/lab/repo<br/>copy of the repo"]
    end
    subgraph VMB["microVM for harness B and scripted (detached)"]
        RUN[runner.py] --> JL["/lab/runs/RUN_ID.jsonl"]
        RUN --> SDK[Claude Agent SDK + CLI<br/>harness B, read-only mount]
        SDK --> REPOB["/lab/repo"]
    end
    DA -->|"one sb.exec per tool call"| REPOA
    SR -->|"exec_detached job: task JSON"| RUN
    RUN -->|JSONL events on stdout, job log| SR
    SDK -->|"HTTPS, placeholder key"| PX
    PX -->|"real key, api.deepseek.com only"| DS[DeepSeek API<br/>Anthropic-compatible]
```

Each session gets its own microVM with a copy of the repo. The repo is cloned (or, for a local fixture, archived) on the host and copied in, so a VM never needs network to get its code. The API hands a user message to the session's runner task, the only process allowed to write that session's events. The runner appends every durable event the harness produces to PostgreSQL, and a single `LISTEN` connection wakes the WebSocket subscribers, who read the new rows from the log. The log is the single source of truth: session state is a fold over its events, and each run starts from the transcript folded from it. Token-by-token deltas (`agent_delta`) only go to live clients and are never stored. The VM is detached, so it keeps running if the API restarts, and the next message reattaches to it (see [Crash recovery, resume and fork](#crash-recovery-resume-and-fork)).

Harnesses sit behind one interface and declare where they run (`placement`):

- **`deepagents_ollama` (placement A, `host`).** A [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) loop runs inside the API process and uses a local Ollama model (default `gemma4:26b-a4b-it-mtp-q4_K_M`) on `127.0.0.1`. Its tools (read, write, edit, ls, glob, grep, shell) all run in the session's VM through `MicrosandboxBackend`, a deepagents backend where every operation is one `sb.exec` or `sb.fs` call. The VM is the agent's hands only: it is created with `Network.none()`, so it cannot reach the internet, the host or Ollama. LangGraph's message stream becomes live `agent_delta` events; tool calls, tool results and the final answer become durable events, and `run_finished` carries the token counts. There is no LangGraph checkpointer; the event log is the only memory.
- **`claude_agent` (placement B, `sandbox`).** The [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) runs inside the VM, driven by `runner.py` with a `ClaudeSDKClient` in the repo; it uses the tools built into the bundled Claude Code CLI (Bash, Read, Edit, Write, Glob, Grep; subagents and web tools are disabled). Its brain is DeepSeek through `https://api.deepseek.com/anthropic` (model `LAB_DEEPSEEK_MODEL`, default `deepseek-flash`). The VM's network allows DNS and `api.deepseek.com` only, and the key never enters it: the guest's `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` hold the placeholder `$MSB_...`, which microsandbox's host proxy swaps for the real `DEEPSEEK_API_KEY` on requests to that host only. The SDK (with its CLI, about 380 MB) is installed once on the host for Linux arm64 (`make claude-sdk`, into `.cache/`) and mounted read-only into each of these VMs, which get 1 GiB. Text deltas from the SDK's stream become `agent_delta`; tool calls, tool results and the final answer become durable events, and `run_finished` carries the token usage and `cost_usd`.
- **`scripted` (placement `sandbox`).** A deterministic fake agent that runs as `runner.py` inside the VM, for tests. It installs pytest, so its VM keeps the default public network.

Both in-VM harnesses use the same runner protocol: `runner.py` writes every durable event to a file in the VM first and then prints it. The runner is a microsandbox managed job (`exec_detached`), not an `exec_stream`: a process started with `exec_stream` dies with the host process that started it, while a job keeps running when the API dies and its output can be followed again after a restart.

Every run has limits (see [Configuration](#configuration)): a wall-time limit, a per-command timeout in the VM, and a maximum number of tool calls (for `claude_agent`, the SDK's `max_turns`). A run that hits one ends with `run_failed{reason: "timeout"}`, `run_failed{reason: "max_tool_calls"}` or `run_failed{reason: "max_turns"}`. `claude_agent` also has a spend limit, `LAB_MAX_USD_PER_RUN`: the runner adds up the cost after every model turn, from the usage in the turn's stream events times the DeepSeek prices in `src/lab/pricing.py`, and over the limit it interrupts the client and ends the run with `run_failed{reason: "budget"}`. The check runs at turn boundaries, so tool calls the model already issued in that turn may still run.

### Harnesses behind one interface, and switching between them

A harness declares its `name`, its `placement` (`host` or `sandbox`), its `capabilities` (`streaming`, `tool_use`, `native_resume`, `cost_report`, `budget`) and the VM it needs (`vm_spec()`: network policy, memory, secrets, mounts). Shared code looks harnesses up in the registry by name and checks only those declarations, never a harness's type; a unit test fails if code outside `src/lab/harnesses/` imports a harness module. A contract test runs both real harnesses with a fake model (deepagents) or a fake SDK (Claude) and checks the same rules on their event streams.

`POST /sessions/{id}/harness {name}` appends `harness_switched`, queued like a message. The next run uses the new harness on the transcript folded from the log (for `claude_agent`, the earlier turns go into the prompt). A VM made for another harness cannot be reused, because its network policy, secrets and mounts are fixed when it is created: it is destroyed (`sandbox_destroyed{reason: "harness_switched"}`) and rebuilt from the repo plus the saved workspace, so the edits of earlier runs carry over. A harness with `native_resume` (`claude_agent`) continues its own SDK session instead, but only when its previous run finished in the same, still running VM; anything that changes the VM or the harness drops that option, and the next run starts from the log again.

### DeepSeek as the brain of Claude Code

Anthropic does not support Claude Code or the Agent SDK with non-Claude models; this works, but is not a supported setup. The lab sends task code to DeepSeek's servers, so only public repos should go through `claude_agent`. Known gaps of DeepSeek's [Anthropic-compatible API](https://api-docs.deepseek.com/guides/anthropic_api) that matter here:

- `anthropic-beta` and `anthropic-version` headers, `cache_control`, `top_k`, `service_tier` and `mcp_servers` are ignored; DeepSeek caches prompt prefixes on its own (cache reads show up as `cache_read_input_tokens`).
- `thinking.budget_tokens` is ignored, and `disable_parallel_tool_use` too, so the model may issue several tool calls per turn.
- `redacted_thinking` and `search_result` content blocks are not supported, and Anthropic's server tools (web search) are not available.
- The CLI does not know the model and says so (`unrecognized_model`); its `total_cost_usd` and `max_budget_usd` assume Claude prices and are not used.
- The per-message `usage` on streamed assistant messages is unreliable (`output_tokens: 0`, repeated per content block). The runner takes per-turn usage from the `message_delta` stream event and the totals from the final `result` message.


### Crash recovery, resume and fork

Three explicit state machines (`src/lab/state.py`) are folded from the log, and an event that a machine does not allow in its current state raises an error instead of being folded silently:

- session: `created`, `ready`, `running`, `idle`, `closed`, plus `failed` (the repo could not be set up for a new session);
- sandbox: `none`, `provisioning`, `attached`, `lost`, `destroyed`;
- run: `queued`, `running`, then `finished`, `failed` or `interrupted`.

The VM's lifecycle is in the log too (`sandbox_provisioning`, `sandbox_attached`, `sandbox_lost`, `sandbox_destroyed`), so a VM that was half set up when the API died is never reused. After every run the session runner saves the VM's working tree as a binary patch against the starting commit (`workspace_saved`). A lost VM, or a fork that cannot copy a live VM, is rebuilt from the repo (an https clone is checked out at the same commit) plus that patch.

When the API starts, every open session's runner task first reconciles its session, before it takes new messages, so it stays the only writer:

```mermaid
flowchart TD
    S[API starts: one runner per open session] --> V{VM in the log<br/>still running?}
    V -->|yes| R
    V -->|no| L[append sandbox_lost<br/>next run rebuilds the VM] --> R
    R{run was running?} -->|no| Q
    R -->|"host harness (loop died with the API)"| I[append run_interrupted]
    R -->|in-VM harness| F[append the events in runs/RUN_ID.jsonl<br/>that the log lacks]
    F --> J{runner job alive?}
    J -->|yes| FO[follow its output to the end] --> T
    J -->|no| T{terminal event?}
    T -->|no| I
    T -->|yes| Q
    I --> Q{last message unanswered,<br/>run at most once so far?}
    Q -->|yes| RR[run it again on the transcript from the log]
    Q -->|no| D[take new messages]
    RR --> D
```

Events from the runner carry `idem_key = <run_id>:<rseq>`, so reading the same file twice, or the file and then the job's output, never appends an event twice. A message is re-run at most once after an interruption, and never if the interrupted run already answered it: a second answer is worse than none. The chaos tests (`make test-chaos`) `kill -9` the API mid-run for both placements and check the result; Hypothesis checks the invariants (never two running runs, gapless seq, at most one answer per message, deterministic fold) over random interleavings of messages, agent steps, crashes and restarts. Measured recovery times are in [docs/spike-results.md](docs/spike-results.md#crash-recovery).

`POST /sessions/{id}/fork {at_seq}` creates a new session whose log is the source's up to `at_seq` plus `forked_from`, copied in one transaction, and gives it its own VM. At the source's head, with a running source VM made for the same harness, the fork's VM is a copy-on-write `fork()` of the source's (memory and disk). Otherwise it is rebuilt from the repo plus the workspace patch saved at or before `at_seq`; this is always the case for `deepagents_ollama` and `claude_agent`, because microsandbox refuses to fork a VM with a network policy of its own (`Network.none()`) or with secrets. A fork in the middle of a run is refused with 409. `DELETE /sessions/{id}` destroys the VM and closes the session; closed sessions are not reconciled.

## Requirements

- Apple Silicon Mac, [uv](https://docs.astral.sh/uv/)
- Python 3.13 (`uv python install 3.13`)
- Docker (for example OrbStack) to run PostgreSQL 18 from `compose.yaml`
- [Ollama](https://ollama.com) on its default `127.0.0.1:11434` with the model in `LAB_OLLAMA_MODEL` pulled (default `gemma4:26b-a4b-it-mtp-q4_K_M`, about 18 GB) for the `deepagents_ollama` harness
- For the `claude_agent` harness, its tests and the network spike: `DEEPSEEK_API_KEY` in the environment, and `make claude-sdk` once
- If the Mac runs an outbound firewall (for example LuLu), allow `.venv/lib/python3.13/site-packages/microsandbox/_bundled/bin/msb`. All guest traffic leaves through that binary.

Docker is only used for Postgres. `uv sync` installs the microsandbox Python SDK, and its wheel bundles the runtime (`msb` plus libkrun), which runs each VM on macOS Hypervisor.framework. No daemon is involved: the API process owns the VMs.

## Commands

```sh
uv sync                # create .venv and install the pinned dependencies from uv.lock
make db-up             # start PostgreSQL (compose.yaml, host port 55432)
make migrate           # apply migrations/*.sql
make api               # run the session API on http://127.0.0.1:8000
make lint              # ruff (lint and format check) and mypy
make test              # unit tests (need Postgres, use their own lab_test database)
make test-integration  # end-to-end tests with real microVMs (need network; the slow agent test needs Ollama, the claude_agent tests DEEPSEEK_API_KEY and spend a few DeepSeek tokens; skipped without them)
make test-chaos        # kill -9 the API mid-run and restart it, per placement; prints recovery times (the host case needs Ollama)
make claude-sdk        # install the Claude Agent SDK for the VMs into .cache/ (once; test-integration runs it)
make spike             # run the three spikes in order
make check-names       # fail if a tracked file matches a pattern in the local .names-denylist
```

A chat from the command line, with `make api` running in another terminal:

```sh
uv run lab new fixtures/calc-repo --harness deepagents_ollama   # prints the session id
uv run lab tail <session_id>                                    # stream events (Ctrl-C to stop)
uv run lab say <session_id> "One test fails. Fix the code."     # in a third terminal
```

The API: `POST /sessions {repo_url, harness}`, `POST /sessions/{id}/messages {text, idem_key}` (202), `POST /sessions/{id}/harness {name}` (202), `POST /sessions/{id}/fork {at_seq}` (201), `DELETE /sessions/{id}` (202), `GET /sessions/{id}/events?after=N`, and `WS /sessions/{id}/stream?after=N`. A message, a switch or a close for a closed or failed session gets 409. Harnesses: `deepagents_ollama`, `claude_agent` and `scripted` (the default). `repo_url` is an `https://` git URL or a directory under `fixtures/`; other schemes are refused, because the clone runs on the host. Neither the `deepagents_ollama` VM (no network) nor the `claude_agent` VM (DeepSeek only) can install anything: the stock `python:3.13` image has no pytest, and a real repo's test dependencies are missing. The `calc-repo` fixture uses `unittest` for that reason.

## Configuration

Read from the environment when the API starts (`src/lab/config.py`).

| Variable | Default | Meaning |
|---|---|---|
| `LAB_DATABASE_URL` | `postgresql://lab:lab@127.0.0.1:55432/lab` | Event log database |
| `LAB_VM_CPUS` | `1` | vCPUs per session VM |
| `LAB_VM_MEMORY_MIB` | `512` | Memory limit per session VM |
| `LAB_RUN_TIMEOUT_S` | `900` | Wall time of one run, including VM creation; then `run_failed{reason: "timeout"}` |
| `LAB_EXEC_TIMEOUT_S` | `120` | One command in the VM; the agent sees exit code 124 and a timeout message |
| `LAB_MAX_TOOL_CALLS` | `50` | Tool calls per run (`deepagents_ollama`); then `run_failed{reason: "max_tool_calls"}`. For `claude_agent` it is the SDK's `max_turns`; then `run_failed{reason: "max_turns"}` |
| `LAB_OLLAMA_MODEL` | `gemma4:26b-a4b-it-mtp-q4_K_M` | Ollama model of `deepagents_ollama` (`qwen3-coder:30b` is the coding-tuned alternative) |
| `LAB_OLLAMA_NUM_CTX` | `65536` | Context window requested from Ollama (it advises 32k or more for tool calling) |
| `DEEPSEEK_API_KEY` | none | DeepSeek key for `claude_agent`; it only reaches microsandbox's host proxy, never a VM |
| `LAB_DEEPSEEK_MODEL` | `deepseek-flash` | DeepSeek model of `claude_agent` (`deepseek-v4-pro` is the strong option); must be in `src/lab/pricing.py` |
| `LAB_MAX_USD_PER_RUN` | `0.50` | Spend limit of one `claude_agent` run, at DeepSeek's peak prices; then `run_failed{reason: "budget"}` |
| `LAB_SCRIPTED_STEP_DELAY_S` | `0` | Pause before each step of the `scripted` agent, so the chaos tests can kill the API mid-run |

## Project structure

| Path | What it is |
|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version` | Python 3.13 project; dependencies pinned by uv |
| `Makefile` | The commands above |
| `compose.yaml` | PostgreSQL 18 for the event log |
| `migrations/` | SQL migrations, applied in file name order by `make migrate` |
| `src/lab/` | The lab package: event store (`events.py`), live fan-out (`fanout.py`), sandbox manager (`sandbox.py`), per-session runner task and fork (`session.py`), state machines (`state.py`), startup reconciliation (`recovery.py`), API (`api.py`), CLI (`cli.py`), limits and model settings (`config.py`), DeepSeek prices (`pricing.py`, with the date they were checked) |
| `src/lab/harnesses/` | Agent harnesses behind one interface (`base.py`), plus the registry: `deepagents_ollama.py` (placement A) and its VM backend `microsandbox_backend.py`, `claude_agent.py` (placement B), and `scripted.py` |
| `src/lab/runner/runner.py` | Copied into each VM; runs the in-VM agent (`scripted` or the Claude Agent SDK), JSON task in, JSONL events out |
| `tests/` | Unit tests, including Hypothesis properties of the state machines under crashes (`test_state_properties.py`) and the harness contract (`test_harness_contract.py`, with a fake `claude_agent_sdk` in `tests/fakes/`); `tests/integration/` runs real microVMs (and, marked `slow`, a real model through Ollama; marked `chaos`, kills of the API process; `test_claude_agent.py` uses DeepSeek) |
| `fixtures/calc-repo/` | A tiny Python package with one failing test, used as a repo by the agent tests. Copied into VMs as a single-commit git repo |
| `spikes/` | Throwaway experiments that answer one question each before the real code is written |
| `spikes/out/` | Raw spike output (JSON and cached downloads). Gitignored |
| `docs/spike-results.md` | Curated spike numbers and the decisions they drove, plus the measured crash recovery times |
| `scripts/check_names.sh` | Name check used by `make check-names` |
| `.cache/` | The Claude Agent SDK for Linux arm64, installed by `make claude-sdk` and mounted into `claude_agent` VMs. Gitignored |
| `.names-denylist` | Local regex list for the name check. Gitignored, never committed |
| `~/.microsandbox/` | Outside the repo: microsandbox's image cache, sandbox database and CA. Created by the SDK |

## Spikes

A spike is a short, disposable experiment that removes a risk before building on it. Each script prints its measurements and writes `spikes/out/<spike>.json`. Every VM it starts is destroyed at the end.

| Script | Question it answers | What it does |
|---|---|---|
| `sandbox_spike.py` | Does microsandbox work on this Mac, and what does a VM cost? | Times cold and warm create, boot to first exec, the `exec` round trip and `fork`; measures memory with 1, 3 and 5 VMs at 512 MiB and 1 GiB; clones a small public repo in a VM and runs its tests |
| `network_spike.py` | Can a VM be locked down, and can the Claude Code CLI inside it use DeepSeek without seeing the key? | Checks that `Network.none()` blocks everything and that an allow-list of `api.deepseek.com` blocks other hosts; registers the key as a host-scoped secret and checks the guest only sees a placeholder; makes one Messages call and runs the CLI on a small tool task; checks two ways to reach the host's Ollama while it stays on `127.0.0.1` |
| `reattach_spike.py` | Does a VM survive if the process that owns it is killed? | Starts a VM running a loop, `kill -9`s its owner, and reattaches from a new process; repeats without `detach()` to show the VM then dies too |
| `guest_probe.py` | Helper, not a spike | Copied into VMs to make HTTP and socket requests and report the result as JSON |
| `_common.py` | Helper, not a spike | Shared timing, output and cleanup code |

`spikes/out/claude-cli/claude` (about 350 MB) is the Linux arm64 build of the Claude Code CLI. `network_spike.py` downloads the `claude-agent-sdk` wheel for Linux, extracts the CLI bundled in it, caches it there, and copies it into the VM. It never runs on the Mac. Delete it at any time; the next run downloads it again.

## License

Apache-2.0
