# sandboxed-agents-lab

A small lab for coding agents that run in per-chat microVMs ([microsandbox](https://github.com/superradcompany/microsandbox)), behind a session layer whose single source of truth is an append-only event log in PostgreSQL.

It started with a set of spikes that check microsandbox on Apple Silicon and measure what a VM costs ([docs/spike-results.md](docs/spike-results.md)). The session layer runs a chat end to end: a session gets its own VM with a copy of the repo, an agent works on it, and every durable event lands in the log and streams to clients. Four harnesses exist: three real coding agents, placed where their vendors designed them to run (deepagents on a local Ollama model, loop on the host and tools in a VM without network; the Claude Agent SDK inside the VM, with DeepSeek as its brain and the API key kept outside the VM; the same in-VM SDK with the host's Ollama as its brain, reached without any VM network), and a scripted fake agent that runs inside the VM, for tests. A chat can switch harness between messages. A session survives a `kill -9` of the API: on restart every open session is reconciled with its VM and with the event file the in-VM runner keeps, and a chat can be forked at any event between runs. An eval pipeline runs a private task suite through the same API on any harness and grades each agent's final diff with hidden tests in a fresh VM the agent never touched. A minimal Next.js page (`web/`) shows a live chat that stays consistent across tabs and reloads.

The reasoning behind the design (single writer, the two harness placements, the recovery algorithm, the grader's threat model, the choice of sandbox) and the hardest state problem are in [docs/design-notes.md](docs/design-notes.md).

## Quick start

On an Apple Silicon Mac with uv and Docker (see [Requirements](#requirements)):

```sh
make demo
```

It starts PostgreSQL, applies the migrations and starts its own API on a free port, creates a chat on `fixtures/calc-repo` (a tiny package with one failing test), sends one message and streams every event to the terminal: the VM being created, each tool call and its result, the answer, the saved workspace, and the VM being destroyed when the chat is closed. The default harness is `scripted`, the deterministic fake agent, so the demo needs no model and costs nothing; it takes a few seconds once the `python:3.13` image is cached. `make demo DEMO_HARNESS=deepagents_ollama` runs a real agent on the local Ollama model instead, which fixes the bug. Stop `make api` before running it: two APIs on one database would both reconcile the same sessions.

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
        BR[Ollama bridge, in the API process<br/>POST /v1/messages only] -->|127.0.0.1| OL
    end
    subgraph VMA["microVM for harness A (detached, no network)"]
        REPOA["/lab/repo<br/>copy of the repo"]
    end
    subgraph VMB["microVM for harness B and scripted (detached)"]
        RUN[runner.py] --> JL["/lab/runs/RUN_ID.jsonl"]
        RUN --> SDK[Claude Agent SDK + CLI<br/>harness B, read-only mount]
        SDK --> REPOB["/lab/repo"]
    end
    subgraph VMC["microVM for B-local (detached, no network)"]
        RUNC[runner.py + Claude Agent SDK<br/>loopback relay to vsock] --> REPOC["/lab/repo"]
    end
    DA -->|"one sb.exec per tool call"| REPOA
    SR -->|"exec_detached job: task JSON"| RUN
    RUN -->|JSONL events on stdout, job log| SR
    SR -->|"exec_detached job"| RUNC
    RUNC -->|"vsock, through a host Unix socket"| BR
    SDK -->|"HTTPS, placeholder key"| PX
    PX -->|"real key, api.deepseek.com only"| DS[DeepSeek API<br/>Anthropic-compatible]
```

Each session gets its own microVM with a copy of the repo. The repo is cloned (or, for a local fixture, archived) on the host and copied in, so a VM never needs network to get its code. The API hands a user message to the session's runner task, the only process allowed to write that session's events. The runner appends every durable event the harness produces to PostgreSQL, and a single `LISTEN` connection wakes the WebSocket subscribers, who read the new rows from the log. The log is the single source of truth: session state is a fold over its events, and each run starts from the transcript folded from it. Token-by-token deltas (`agent_delta`) only go to live clients and are never stored. The VM is detached, so it keeps running if the API restarts, and the next message reattaches to it (see [Crash recovery, resume and fork](#crash-recovery-resume-and-fork)).

Harnesses sit behind one interface and declare where they run (`placement`):

- **`deepagents_ollama` (placement A, `host`).** A [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) loop runs inside the API process and uses a local Ollama model (default `gemma4:26b-a4b-it-mtp-q4_K_M`) on `127.0.0.1`. Its tools (read, write, edit, ls, glob, grep, shell) all run in the session's VM through `MicrosandboxBackend`, a deepagents backend where every operation is one `sb.exec` or `sb.fs` call. The VM is the agent's hands only: it is created with `Network.none()`, so it cannot reach the internet, the host or Ollama. LangGraph's message stream becomes live `agent_delta` events; tool calls, tool results and the final answer become durable events, and `run_finished` carries the token counts. There is no LangGraph checkpointer; the event log is the only memory.
- **`claude_agent` (placement B, `sandbox`).** The [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) runs inside the VM, driven by `runner.py` with a `ClaudeSDKClient` in the repo; it uses the tools built into the bundled Claude Code CLI (Bash, Read, Edit, Write, Glob, Grep; subagents and web tools are disabled). Its brain is DeepSeek through `https://api.deepseek.com/anthropic` (model `LAB_DEEPSEEK_MODEL`, default `deepseek-flash`). The VM's network allows DNS and `api.deepseek.com` only, and the key never enters it: the guest's `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` hold the placeholder `$MSB_...`, which microsandbox's host proxy swaps for the real `DEEPSEEK_API_KEY` on requests to that host only. The SDK (with its CLI, about 380 MB) is installed once on the host for Linux arm64 (`make claude-sdk`, into `.cache/`) and mounted read-only into each of these VMs, which get 1 GiB. Text deltas from the SDK's stream become `agent_delta`; tool calls, tool results and the final answer become durable events, and `run_finished` carries the token usage and `cost_usd`.
- **`claude_agent_local` (B-local, placement `sandbox`).** The same in-VM Claude Agent SDK and runner as `claude_agent`, with the host's Ollama model (`LAB_OLLAMA_MODEL`, the same brain as `deepagents_ollama`) as its brain, so `deepagents_ollama` against `claude_agent_local` compares two harnesses on one model. Ollama has no auth and stays on `127.0.0.1`. The VM is created with `Network.none()` (no DNS, no IP traffic) and gets one vsock port, which microsandbox connects to a Unix socket on the host (`~/.microsandbox/lab-ollama.sock`). Behind it, a small bridge in the API process passes `POST /v1/messages` (Ollama's Anthropic-compatible endpoint) to Ollama and answers 403 to everything else, so an agent in the VM cannot pull, push, create or delete models through Ollama's own API. Inside the VM the runner listens on a loopback port and relays each connection to the vsock port, because the CLI only speaks TCP. No key of any kind enters the VM (the CLI gets a dummy `ANTHROPIC_API_KEY` so it skips its login), and `run_finished.cost_usd` is 0. The bridge lives in the API process: while the API is down an in-VM run cannot reach its brain, and the restarted API serves the same socket path again, which an existing VM reaches without being recreated (tested). How the CLI copes with a model call that fails during that gap is not tested. A microsandbox `host` network rule for port 11434 also reaches Ollama (see [docs/spike-results.md](docs/spike-results.md#b-local-ollama-from-a-vm-while-it-stays-on-127001)), but it gives the VM a TCP path to Ollama's whole API, so it is not used.
- **`scripted` (placement `sandbox`).** A deterministic fake agent that runs as `runner.py` inside the VM, for tests. It installs pytest, so its VM keeps the default public network.

All in-VM harnesses use the same runner protocol: `runner.py` writes every durable event to a file in the VM first and then prints it. The runner is a microsandbox managed job (`exec_detached`), not an `exec_stream`: a process started with `exec_stream` dies with the host process that started it, while a job keeps running when the API dies and its output can be followed again after a restart.

Every run has limits (see [Configuration](#configuration)): a wall-time limit, a per-command timeout in the VM, and a maximum number of tool calls. The Claude harnesses count the tool calls in each model turn and stop before a turn would pass the cap, since one turn can ask for several calls; the SDK's `max_turns` is only a backstop. A run that hits one ends with `run_failed{reason: "timeout"}` or `run_failed{reason: "max_tool_calls"}`. `claude_agent` also has a spend limit (`claude_agent_local` is free and has none), `LAB_MAX_USD_PER_RUN`: the runner adds up the cost after every model turn, from the usage in the turn's stream events times the DeepSeek prices in `src/lab/pricing.py`, and over the limit it interrupts the client and ends the run with `run_failed{reason: "budget"}`. The check runs at turn boundaries, so tool calls the model already issued in that turn may still run.

### Harnesses behind one interface, and switching between them

A harness declares its `name`, its `placement` (`host` or `sandbox`), its `capabilities` (`streaming`, `tool_use`, `native_resume`, `cost_report`, `budget`) and the VM it needs (`vm_spec()`: network policy, memory, secrets, mounts, vsock ports). Shared code looks harnesses up in the registry by name and checks only those declarations, never a harness's type; a unit test fails if code outside `src/lab/harnesses/` imports a harness module. A contract test runs the three real harnesses with a fake model (deepagents) or a fake SDK (both Claude harnesses) and checks the same rules on their event streams.

`POST /sessions/{id}/harness {name}` appends `harness_switched`, queued like a message. The next run uses the new harness on the transcript folded from the log (for the Claude harnesses, the earlier turns go into the prompt). A VM made for another harness cannot be reused, because its network policy, secrets and mounts are fixed when it is created: it is destroyed (`sandbox_destroyed{reason: "harness_switched"}`) and rebuilt from the repo plus the saved workspace, so the edits of earlier runs carry over. A harness with `native_resume` (`claude_agent`, `claude_agent_local`) continues its own SDK session instead, but only when its previous run finished in the same, still running VM; anything that changes the VM or the harness drops that option, and the next run starts from the log again.

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

`POST /sessions/{id}/fork {at_seq}` creates a new session whose log is the source's up to `at_seq` plus `forked_from`, copied in one transaction, and gives it its own VM. At the source's head, with a running source VM made for the same harness, the fork's VM is a copy-on-write `fork()` of the source's (memory and disk). Otherwise it is rebuilt from the repo plus the workspace patch saved at or before `at_seq`; this is always the case for `deepagents_ollama`, `claude_agent` and `claude_agent_local`, because microsandbox refuses to fork a VM with a network policy of its own (`Network.none()`) or with secrets. A fork in the middle of a run is refused with 409. `DELETE /sessions/{id}` destroys the VM and closes the session; closed sessions are not reconciled.

## Evals

`make eval` measures which harness and model solve small coding tasks, and at what cost. Every trial goes through the normal session API, so the eval also exercises the session layer.

```mermaid
flowchart LR
    T["evals/tasks/TASK<br/>instruction.md, environment/"] --> R[eval runner]
    R -->|"POST /sessions on environment/<br/>instruction as the one message, then close"| API[session API]
    API --> VM[agent VM<br/>single-commit repo, harness's own network policy]
    VM -->|"workspace_saved: the final diff"| PG[(event log)]
    PG -->|diff| G[grader]
    H["tests/ (hidden), config.json"] --> G
    G -->|"apply diff, run hidden tests"| GV[fresh grader VM<br/>no network, pytest mounted read-only]
    G -->|"pass, fail, rejected or infra_error"| J["evals/results/DATE.jsonl and .md"]
```

**Tasks.** `evals/tasks/` holds 10 small tasks written for this lab and never published before: bug fixes and small features in tiny Python packages (slugs, interval merging, duration parsing, CSV statistics, an LRU cache, SemVer precedence, deep merging, word wrapping, a token bucket, an inventory). The layout follows Harbor's, so the tasks could be ported: `instruction.md` (the prompt), `environment/` (the repo at its starting commit), `tests/` (hidden tests, plus `config.json` with the `fail_to_pass` and `pass_to_pass` test ids), `solution/solution.diff` (a reference diff), and `adversarial/hardcode.diff` (a diff that special-cases the instruction's example, for the grader check). 10 tasks is below the 20 to 50 Anthropic suggests for an agent eval, so only large differences between harnesses are detectable.

**Hard tier.** Those 10 tasks each name the function, show the failing input and fit in one function, and the first run solved nearly all of them, so they cannot separate harnesses. Five more tasks (`evals/tasks/hard-*`) work on one shared app instead: `evals/repos/ledger/`, a standard-library invoicing app of about 20 modules (models, money and tax, CSV and JSON parsers, a JSON store, monthly reports, payment reconciliation, export formats behind a registry, a CLI) with its own `unittest` suite and some noise (docs, a changelog, a deprecated module, helpers nothing calls). Each instruction is a user report: the symptom, no file or function names.

| Task | Report | What it takes |
|---|---|---|
| `hard-month-boundary` | The March report for Berlin lists an invoice from April 1st | Two places use one fixed UTC offset across a DST change: the monthly report's month bounds and a yearly per-month summary; hidden tests cover several months and time zones and check that the two agree |
| `hard-rename-field` | Rename `customer_id` to `account_id` everywhere; old files must still load | Model, both parsers, the store, every export format, the CLI and the visible tests; hidden tests cover each layer and old files |
| `hard-cent-drift` | Totals are sometimes a cent off the sum of the lines; CSV and JSON imports disagree | The total is rounded once instead of per line, and the JSON path goes through `float` |
| `hard-new-export` | Add a Markdown export | The exporter registry convention (name and file suffix: `--format` choices and `--output x.md`), the CSV columns, right-aligned amounts, escaping `\|`, no new dependency |
| `hard-reconcile-speed` | Reconciling a year of payments never finishes | The matcher is quadratic; the fix must keep results and order identical (checked against the rules on 400 random cases) and match 100,000 invoices with 100,000 payments within 30 s in the grader VM (the reference takes 0.35 s) |

`scripts/build_hard_tasks.py` generates each hard task's `environment/` from the ledger plus the task's `start.diff` (the planted bug; the feature tasks have none), and the visible test copies the grader runs from the environment with the reference solution applied (so `hard-rename-field` gets the renamed ones). The generated files are committed and `--check` (also a unit test) confirms they match. For a hard task, `adversarial/hardcode.diff` is a plausible partial fix (only March, only the rounding, no escaping, no ordering) and must fail like the easy tier's hardcoded diffs.

**Calibration.** Before any comparison, `deepagents_ollama` (Gemma 4) ran each hard task 3 times. Two tasks went 3/3 and were hardened (the DST bug now also sits in a yearly summary; the export also has to be picked by the output file's suffix), and `hard-rename-field` went 0/3 because all three trials also renamed a report attribute that a visible test still used, so the instruction and that test were fixed. The re-run of those three tasks, with the other two kept from the first run: 9 of 15 trials, 60% (95% CI 27% to 87%), against 93% on the easy tier. `hard-new-export` stayed at 3/3 after hardening and is kept as the floor task. The superseded trials are in `evals/results/2026-11-17-superseded.jsonl`.

**What the agent sees.** A session can be created on a task's `environment/` and on nothing else under `evals/` (the API refuses the task directory itself, which holds the hidden tests). The VM gets a fresh single-commit repo built from it: no history, tags, reflogs or remotes. The visible tests use `unittest`, because the agent VMs cannot install anything. The network policy is the harness's normal one, and every harness gets the same VM (1 vCPU, 1 GiB), run timeout, command timeout and tool-call cap. The agent's final diff is the `workspace_saved` patch the session layer records after every run.

**Grader** (`src/lab/evals/grader.py`):

1. Only the final diff counts.
2. The diff is rejected if it touches a hidden test path, any `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, a `tool.pytest` table of a `pyproject.toml`, `sitecustomize.py`, `usercustomize.py`, a `*.pth` file, CI config or a lockfile, or if it holds a binary file, a symlink, a mode change or a path outside the repo. A diff that cannot be parsed is rejected too.
3. Otherwise it is applied in a fresh VM built from `environment/`, with no network at all and pytest mounted read-only from the host (`make grader-deps`).
4. The hidden tests are copied to `/grader`, outside the repo, and checked against their sha256. pytest runs with the grader's config file and root directory and without its cache, so no file in the repo can configure it; it starts in isolated mode with the repo last on `sys.path`, so a module in the diff cannot shadow pytest or the standard library. A small plugin records each test's outcome by node id.
5. Pass means every `fail_to_pass` and `pass_to_pass` test passed and pytest exited 0. No collected tests, a crash or unreadable results are a fail.
6. A VM that cannot be created is an `infra_error`, not a fail, and the trial (or the grading) is retried, up to 3 times.

Pass or fail is a test result, not a model's opinion. The limit of this design: the agent's code runs inside the grader's pytest process (it is the code under test), so code written to attack the grader could forge its results. Reading transcripts (`lab replay`) is the check for that.

`make eval-check` runs the grader's adversarial suite against every task, and `make eval` runs it first. For each task: the reference diff passes; an empty diff fails with exactly the `fail_to_pass` tests failing (so the task is valid); a diff that edits a hidden test path is rejected; a diff that adds a `conftest.py` forcing every test to pass is rejected; the hardcoded diff fails; and a run that collects zero tests fails. A grader VM takes under a second with a warm image cache; the whole suite for the 15 tasks (90 grades, 60 of them in VMs, 4 at a time) takes about 36 s.

**Report.** Each trial is appended to `evals/results/<date>.jsonl` (task, harness, model, verdict, input, output and cache tokens, cost, pricing date, wall time, tool calls, infra errors, session id), and `evals/results/<date>.md` is rebuilt from the whole day's file: a per-task table of solved trials per harness and model; per tier (easy, hard) and combined, the solve rate as the mean of per-task rates with a 95% interval from a two-stage bootstrap over tasks and then trials (10,000 resamples; a pooled interval would treat repeats of one task as independent and be too narrow); pass@3 and pass^3; the paired per-task difference between every two harnesses with its bootstrap interval; median and p95 tokens; USD per solved task (all spend, failed trials included, divided by solved trials); p50 and p95 wall time, which includes VM creation and model latency; and every trial with its session id. `lab replay <session>` prints a trial's whole log, tool results and the saved diff unclipped, for reviewing failed trials and checking passed ones for reward hacking.

Results, 2026-11-17 ([report](evals/results/2026-11-17.md)), 3 trials per task and harness, every harness capped at 50 tool calls and 900 s per run:

| Harness (brain) | Easy (10 tasks) | Hard (5 tasks) | Wall p50 | Cost |
|---|---|---|---|---|
| `deepagents_ollama` (Gemma 4 `gemma4:26b-a4b-it-mtp-q4_K_M`, local) | 28/30, 93% (80 to 100%) | 9/15, 60% (27 to 87%) | 218 s | $0 |
| `claude_agent` (DeepSeek `deepseek-flash`) | 30/30, 100% | 15/15, 100% | 21 s | $0.57 in all, $0.013 per solved task |

The paired difference per task is 40 points on the hard tier (95% interval 13 to 73) and 7 points on the easy tier (0 to 20), so only the hard tier separates the two. Gemma's six hard failures: three runs hit the 900 s limit, one hit the 50-call cap, two ended with an incomplete fix. The grader judges the final diff, not how the run ended: two DeepSeek `hard-rename-field` runs and one Gemma easy run were cut by a limit after the code was already right, and pass. A first DeepSeek run on the hard tier, before `claude_agent` counted tool calls (it capped turns, and one turn can ask for several calls), also solved 15 of 15, using up to 84 calls ($0.41; kept in `evals/results/2026-11-17-superseded.jsonl`). `claude_agent_local` (the Agent SDK on the local Gemma) has run one trial through the eval so far.

## Measured numbers

On an Apple M5 Pro with 48 GiB and macOS 27.2.1, microsandbox 0.7.15. Details, sample sizes and how each was measured are in [docs/spike-results.md](docs/spike-results.md).

| What | Value |
|---|---|
| VM create, image cached (n=3) | p50 194 ms |
| VM create, cold, including the image pull (`python:3.13-slim`) | 2188 ms |
| Boot to first exec, warm (n=3) | p50 34 ms |
| `sb.exec` round trip, the host loop's cost per tool call (n=50) | p50 0.7 ms, p95 1.2 ms |
| `fork` of a running VM | 319 ms |
| Host memory per idle VM (`msb` RSS), 1 to 5 VMs | about 115 MiB at 512 MiB, about 126 MiB at 1 GiB |
| Grader VM, image cached | under 1 s |
| API answering again after `kill -9` and restart (3 runs per case) | 0.44 to 0.45 s |
| Events written by the in-VM runner while the API was down, in the log after the restart | 0.54 to 0.55 s |
| Reconciling one session, no runner to follow | 13 to 17 ms |

Memory under an agent workload (git, pytest, the Agent SDK CLI) is not measured. The eval numbers are in [Evals](#evals).

## Limitations

- **One Mac, one API process.** Everything runs on Apple Silicon. The single-writer guarantee assumes one API process per database: the advisory try-lock only catches overlapping transactions, and several instances would need leases with a fencing token ([design notes](docs/design-notes.md#one-writer-per-session)). There is no auth and no notion of users.
- **microsandbox is beta.** The version is pinned (0.7.15). Known open issues that affect the lab: DNS inside VMs can break after the Mac changes network (#1998), memory freed in a guest is not returned to the host (#1938), so long-lived VMs only grow, and large image pulls can abort (#2006). VMs belong to the process that creates them; detaching is what keeps them alive across an API crash.
- **Agent VMs cannot install anything.** None of the real harnesses' VMs can reach a package index, and the stock `python:3.13` image has no pytest, so the agent can read and edit a real repo but usually cannot run its test suite. The fixtures and eval tasks use `unittest` for that reason.
- **Recovery has known edges.** Edits made after the last workspace snapshot are lost with the VM. A re-run after an interruption gets the conversation, not the dead run's tool calls. A run that starts on the source between a fork's head check and `fork()` is not guarded. There is no chaos test for `claude_agent` (it shares the runner job and recovery path with `scripted`, which has them), and how the CLI copes with a failed model call while the API, and with it the Ollama bridge, is down is not tested.
- **The eval is small.** 15 tasks, below the 20 to 50 Anthropic suggests, so only large differences are detectable; the bootstrap intervals say how large. `claude_agent_local` has run one trial. DeepSeek costs are at peak prices, so they err high.
- **The grader trusts its process.** The code under test runs inside the grader's pytest process and could forge results if written to attack it; transcript review is the control.
- **DeepSeek behind Claude Code is unsupported.** It works, with the gaps listed above, and it sends task code to DeepSeek's servers, so only public repos should go through `claude_agent`.

## Requirements

- Apple Silicon Mac, [uv](https://docs.astral.sh/uv/)
- Python 3.13 (`uv python install 3.13`)
- Docker (for example OrbStack) to run PostgreSQL 18 from `compose.yaml`
- [Ollama](https://ollama.com) on its default `127.0.0.1:11434` with the model in `LAB_OLLAMA_MODEL` pulled (default `gemma4:26b-a4b-it-mtp-q4_K_M`, about 18 GB) for the `deepagents_ollama` and `claude_agent_local` harnesses. `claude_agent_local` uses Ollama's Anthropic-compatible `/v1/messages`, which needs a recent Ollama (tested with 0.40.16)
- For the `claude_agent` and `claude_agent_local` harnesses: `make claude-sdk` once. For `claude_agent`, its tests and the network spike also `DEEPSEEK_API_KEY` in the environment
- If the Mac runs an outbound firewall (for example LuLu), allow `.venv/lib/python3.13/site-packages/microsandbox/_bundled/bin/msb`. All guest traffic leaves through that binary.

Docker is only used for Postgres. `uv sync` installs the microsandbox Python SDK, and its wheel bundles the runtime (`msb` plus libkrun), which runs each VM on macOS Hypervisor.framework. No daemon is involved: the API process owns the VMs.

## Commands

```sh
uv sync                # create .venv and install the pinned dependencies from uv.lock
make db-up             # start PostgreSQL (compose.yaml, host port 55432)
make migrate           # apply migrations/*.sql
make api               # run the session API on http://127.0.0.1:8000
make demo              # one chat on fixtures/calc-repo, streamed to the terminal (DEMO_HARNESS, default scripted)
make lint              # ruff (lint and format check) and mypy
make test              # unit tests (need Postgres, use their own lab_test database)
make test-integration  # end-to-end tests with real microVMs (need network; the slow agent tests and the claude_agent_local tests need Ollama, the claude_agent tests DEEPSEEK_API_KEY and spend a few DeepSeek tokens; skipped without them)
make test-chaos        # kill -9 the API mid-run and restart it, per placement; prints recovery times (the host case needs Ollama)
make claude-sdk        # install the Claude Agent SDK for the VMs into .cache/ (once; test-integration runs it)
make grader-deps       # install pytest for the grader VMs into .cache/ (once; eval-check runs it)
make eval-check        # the grader's adversarial suite against every task (real VMs, no model)
make eval              # eval-check, then every task 3 times per harness; stop `make api` first
make spike             # run the three spikes in order
make web-test          # Playwright tests of the chat page (its own API and page, real VMs)
make check-names       # fail if a tracked file matches a pattern in the local .names-denylist
```

A chat from the command line, with `make api` running in another terminal:

```sh
uv run lab new fixtures/calc-repo --harness deepagents_ollama   # prints the session id
uv run lab tail <session_id>                                    # stream events (Ctrl-C to stop)
uv run lab say <session_id> "One test fails. Fix the code."     # in a third terminal
uv run lab replay <session_id>                                  # the whole log, nothing clipped
```

An eval run, on the local harnesses only (no DeepSeek spend), on two tasks, once each:

```sh
make eval EVAL_HARNESS=deepagents_ollama,claude_agent_local EVAL_TASKS=lru-recency,word-wrap EVAL_REPEATS=1
```

`EVAL_HARNESS` (default `deepagents_ollama,claude_agent`) takes any registered harnesses, `EVAL_TASKS` (default: all) task names or shell patterns (`EVAL_TASKS='hard-*'` runs the hard tier), and `EVAL_REPEATS` (default 3) the trials per task and harness. The runner starts its own API process on a free port, against the database in `LAB_DATABASE_URL`, so `make api` must not run at the same time: both would reconcile the same open sessions. Every eval session is closed after its run, which destroys its VM. To review a trial afterwards, start `make api` and use `lab replay`.

The API: `POST /sessions {repo_url, harness}`, `POST /sessions/{id}/messages {text, idem_key}` (202), `POST /sessions/{id}/harness {name}` (202), `POST /sessions/{id}/fork {at_seq}` (201), `DELETE /sessions/{id}` (202), `GET /sessions/{id}/events?after=N`, and `WS /sessions/{id}/stream?after=N`. A message, a switch or a close for a closed or failed session gets 409. Harnesses: `deepagents_ollama`, `claude_agent`, `claude_agent_local` and `scripted` (the default). `repo_url` is an `https://` git URL, a directory under `fixtures/` or an eval task's `environment/`; other schemes are refused, because the clone runs on the host. None of the `deepagents_ollama` VM (no network), the `claude_agent` VM (DeepSeek only) and the `claude_agent_local` VM (no network, Ollama's Messages API over vsock) can install anything: the stock `python:3.13` image has no pytest, and a real repo's test dependencies are missing. The `calc-repo` fixture uses `unittest` for that reason.

## Configuration

Read from the environment when the API starts (`src/lab/config.py`).

| Variable | Default | Meaning |
|---|---|---|
| `LAB_DATABASE_URL` | `postgresql://lab:lab@127.0.0.1:55432/lab` | Event log database |
| `LAB_VM_CPUS` | `1` | vCPUs per session VM |
| `LAB_VM_MEMORY_MIB` | `512` | Memory limit per session VM |
| `LAB_RUN_TIMEOUT_S` | `900` | Wall time of one run, including VM creation; then `run_failed{reason: "timeout"}` |
| `LAB_EXEC_TIMEOUT_S` | `120` | One command in the VM; the agent sees exit code 124 and a timeout message |
| `LAB_MAX_TOOL_CALLS` | `50` | Tool calls per run, for every harness; then `run_failed{reason: "max_tool_calls"}`. The Claude harnesses also pass it to the SDK as `max_turns`, as a backstop |
| `LAB_OLLAMA_MODEL` | `gemma4:26b-a4b-it-mtp-q4_K_M` | Ollama model of `deepagents_ollama` and `claude_agent_local` (`qwen3-coder:30b` is the coding-tuned alternative) |
| `LAB_OLLAMA_NUM_CTX` | `65536` | Context window `deepagents_ollama` requests from Ollama (it advises 32k or more for tool calling). `claude_agent_local` cannot request one through the Messages API: it gets the context Ollama serves the model with |
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
| `src/lab/harnesses/` | Agent harnesses behind one interface (`base.py`), plus the registry: `deepagents_ollama.py` (placement A) and its VM backend `microsandbox_backend.py`, `claude_agent.py` (placement B), `claude_agent_local.py` (B-local, with the Ollama bridge), and `scripted.py` |
| `src/lab/evals/` | The eval pipeline: task loader (`tasks.py`), grader and its rules (`grader.py`), the pytest entry point that runs inside the grader VM (`pytest_entry.py`), the adversarial suite (`check.py`), the runner (`run.py`), statistics (`stats.py`) and the Markdown report (`report.py`) |
| `src/lab/runner/runner.py` | Copied into each VM; runs the in-VM agent (`scripted` or the Claude Agent SDK, plus the loopback-to-vsock relay for B-local), JSON task in, JSONL events out |
| `tests/` | Unit tests, including Hypothesis properties of the state machines under crashes (`test_state_properties.py`), the harness contract (`test_harness_contract.py`, with a fake `claude_agent_sdk` in `tests/fakes/`), the grader's rejection rules (`test_grader_rules.py`), the hard tier's generated files, task patterns and per-tier report (`test_hard_tasks.py`) and the eval statistics (`test_eval_stats.py`, the bootstrap against its closed-form variance); `tests/integration/` runs real microVMs (and, marked `slow`, a real model through Ollama; marked `chaos`, kills of the API process; `test_claude_agent.py` uses DeepSeek, `test_claude_agent_local.py` the host's Ollama) |
| `evals/tasks/` | The eval tasks, one directory each (instruction, environment, hidden tests, reference and adversarial diffs; `hard-*` also a `start.diff`) |
| `evals/repos/ledger/` | The invoicing app the hard tier is generated from. Never given to an agent as is |
| `evals/results/` | Eval results: one JSONL file and one Markdown report per day |
| `fixtures/calc-repo/` | A tiny Python package with one failing test, used as a repo by the agent tests. Copied into VMs as a single-commit git repo |
| `spikes/` | Throwaway experiments that answer one question each before the real code is written |
| `spikes/out/` | Raw spike output (JSON and cached downloads). Gitignored |
| `docs/spike-results.md` | Curated spike numbers and the decisions they drove, plus the measured crash recovery times |
| `docs/design-notes.md` | Why the lab is built this way: the hardest state problem, single writer, harness placements, state as a fold, the grader's threat model, the sandbox choice |
| `web/` | The Next.js chat page and its Playwright tests (see [web/README.md](web/README.md)) |
| `scripts/check_names.sh` | Name check used by `make check-names` |
| `scripts/demo.py` | `make demo`: one chat through its own API, streamed with the CLI's renderer |
| `scripts/build_hard_tasks.py` | Generates the hard tasks' `environment/` and visible test copies from `evals/repos/ledger/` (`--check` compares instead) |
| `.cache/` | The Claude Agent SDK for Linux arm64, installed by `make claude-sdk` and mounted into `claude_agent` and `claude_agent_local` VMs, and pytest for the grader VMs (`make grader-deps`). Gitignored |
| `.names-denylist` | Local regex list for the name check. Gitignored, never committed |
| `~/.microsandbox/` | Outside the repo: microsandbox's image cache, sandbox database and CA. Created by the SDK. While the API runs it also holds `lab-ollama.sock`, the Ollama bridge's socket |

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
