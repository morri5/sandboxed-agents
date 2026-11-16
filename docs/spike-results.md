# Spike results

Measured on 2026-11-16 in one full run of `make spike` (after the host firewall allowed the `msb` binary). Every number comes from that run unless a sample size is given. Anything not measured is marked **not measured**.

| | |
|---|---|
| Host | Apple M5 Pro, 48 GiB RAM, macOS 27.2.1 (build 26B131) |
| microsandbox | 0.7.15 (Python SDK, runtime bundled in the wheel) |
| Guest image | `python:3.13-slim` (Linux 6.12, aarch64) |
| VM defaults | 1 vCPU, 512 MiB unless stated |
| Model for the B checks | `deepseek-flash` |

## Status

| Question | Result |
|---|---|
| Does microsandbox run on macOS 27 / M5? | **Yes.** VMs boot, exec, fork, detach and reattach. |
| Clone and test a repo inside a VM | **Works.** |
| Secret proxy with the Agent SDK CLI and DeepSeek | **Works.** The guest only sees a placeholder; DeepSeek accepts the substituted key. |
| Reattach after the owner is SIGKILLed | **Works** with `detached=True` plus `detach()`. |
| B-local: reach Ollama on 127.0.0.1 from a VM | **Works** through both paths tried. |

## VM lifecycle (`spikes/sandbox_spike.py`)

| Metric | Value |
|---|---|
| Cold create, including the image pull | 2188 ms (5212 ms in an earlier run with a cold registry cache) |
| Warm create (image cached), n=3 | p50 194 ms, max 196 ms |
| Boot to first exec (create returned, then `exec("true")`), cold | 37 ms |
| Boot to first exec, warm, n=3 | p50 34 ms (min 17 ms, max 36 ms) |
| `sb.exec("true")` round trip, n=50 | p50 0.7 ms, p95 1.2 ms, max 27.9 ms |
| `fork` of a running VM | 319 ms |

The fork saw the files written in the source VM before the fork, and a file written in the fork did not appear in the source.

The exec round trip is the per-tool-call cost of the host-loop harness. At about 1 ms (p95), it is far below the 50 ms threshold the plan set for batching read-only calls, so no batching is needed.

## Memory per VM, idle after boot and one exec

`guest resident` is `metrics().memory_host_resident_bytes`; `msb RSS` is the RSS of the host `msb machine` process for that VM (from `ps`).

| Memory limit | VMs running | Concurrent create (wall) | Guest resident, mean | msb RSS, mean | msb RSS, max |
|---|---|---|---|---|---|
| 512 MiB | 1 | 238 ms | 49.3 MiB | 117.0 MiB | 117.0 MiB |
| 512 MiB | 3 | 301 ms | 48.2 MiB | 115.2 MiB | 115.2 MiB |
| 512 MiB | 5 | 368 ms | 48.2 MiB | 115.0 MiB | 115.1 MiB |
| 1 GiB | 1 | 268 ms | 58.9 MiB | 125.8 MiB | 125.8 MiB |
| 1 GiB | 3 | 295 ms | 58.9 MiB | 125.7 MiB | 125.8 MiB |
| 1 GiB | 5 | 369 ms | 58.9 MiB | 125.9 MiB | 126.0 MiB |

Idle VMs cost about 115 to 126 MiB of host RSS each, independent of how many run. Memory under an agent workload (git, pytest, the Agent SDK CLI) is **not measured**.

## Clone and test inside a VM

| Step | Value |
|---|---|
| `apt-get install git` plus `pip install pytest` | 20.7 s |
| `git clone https://github.com/pallets/markupsafe` | 1.2 s |
| `pytest` | 216 ms, `39 passed, 41 skipped` |

The 20.7 s install runs on every fresh VM. A lab image with git and pytest preinstalled removes it.

## Guest egress and the host firewall

The first runs failed: guests resolved DNS, but no TCP connection to the internet received data. The cause was the host's outbound firewall (LuLu): guest traffic leaves through `.venv/.../microsandbox/_bundled/bin/msb`, which had no allow rule, while image pulls run inside the Python process, which had one. After an Allow rule for `msb`, everything below passed.

Consequence: with that rule in place, microsandbox's per-VM network policy is the only barrier between a VM and the internet. The checks in the next section confirm the policy holds.

## Network isolation and the secret proxy (`spikes/network_spike.py`)

| Check | Result |
|---|---|
| `Network.none()`: `http://example.com` and `https://example.com` | Blocked |
| DNS plus `api.deepseek.com` only: `https://example.com` | Blocked by the policy proxy: HTTP 403 `network_policy_denied` |
| Guest env with the key registered as a host-scoped secret | `ANTHROPIC_AUTH_TOKEN=$MSB_ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY=$MSB_ANTHROPIC_API_KEY`; the real key appears nowhere in `env` |
| Proxy CA in the guest | Set up by microsandbox without extra config: `NODE_EXTRA_CA_CERTS=/.msb/tls/ca.pem`, and the CA is in the system bundle that `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` point to |
| `GET https://api.deepseek.com/models` through the proxy | 200, models `deepseek-flash`, `deepseek-v4-pro` |
| One minimal Messages API call with `curl` | 200, reply `ok`, 37 input / 12 output tokens (10 of them thinking) |
| Bundled Agent SDK CLI version | `2.1.325 (Claude Code)`, copied into the VM in 2.1 s |
| CLI tool task (`create hello.txt containing ok, then cat it`) | Exit 0 in 8.3 s, 4 turns, tool calls `Write`, `Write`, `Bash`, `hello.txt` contains `ok` |
| Token usage of that task (final `result` message) | 11 646 input, 33 792 cache read, 613 output (398 thinking) |

### Token usage per turn with DeepSeek

The final `result` message carries correct totals. The per-message `usage` on streamed assistant messages does not: each turn appears once per content block with the same input count and `output_tokens: 0`. Phase 5 must therefore take usage from the `result` message (or de-duplicate assistant messages by message id and ignore their output counts), not sum the per-message usage.

The CLI also reports `total_cost_usd: 0.0656`. That figure uses Claude prices and is wrong for DeepSeek, as the plan expected; Phase 5 computes cost from tokens and `src/lab/pricing.py`.

### Secret-proxy decision

**Chosen: host-scoped secret.** The DeepSeek key stays on the host. microsandbox's proxy substitutes it only for requests to `api.deepseek.com`, the guest sees a placeholder, the proxy CA is trusted by curl, Python and Node without extra setup, and the bundled CLI completes a tool-using task through it. The fallback (key as a VM env var) is not needed.

## Reattach after SIGKILL (`spikes/reattach_spike.py`)

An owner process creates a VM, starts a loop that appends a line to a file every second, and is killed with SIGKILL 3 s later. A new process reattaches 5 s after the kill.

| Case | msb process 5 s after the kill | Status | Reattach | Loop |
|---|---|---|---|---|
| `detached=True` + `detach()` | alive | `running` | `Sandbox.get` + `connect()` in 8.7 ms | still running (9 lines at reattach, 12 three seconds later) |
| defaults (not detached) | gone | `stopped` | `connect()` fails: `SandboxNotRunningError` | stopped with the VM |

A non-detached VM dies with its owner, even on SIGKILL (the `msb` child watches its parent through `--parent-watch-fd`). The sandbox record survives as `stopped`.

## B-local: Ollama from a VM while it stays on 127.0.0.1

Ollama listened only on `127.0.0.1:11434` throughout; no host configuration was changed.

| Path | Result |
|---|---|
| Host Unix socket bridged over vsock (`vsock={socket: 11434}`), VM with `Network.none()` | Works: `GET /api/tags` returns 200. Needs a host-side forwarder from the Unix socket to `127.0.0.1:11434` (in the spike, a small asyncio server), and the guest must speak `AF_VSOCK` |
| `host` rule for TCP port 11434 only, to `host.microsandbox.internal` | Works: `GET /api/tags` returns 200 |

Both paths work, so B-local stays possible.

## Crash recovery

Measured on 2026-11-16 with `make test-chaos`, three runs. Each test `kill -9`s the API process in the middle of a run (right after the first durable `tool_call`), restarts it on the same port and watches the log in Postgres. Times count from the moment the new process is started.

| Case | API answers HTTP | Recovery in the log | Outcome |
|---|---|---|---|
| In-VM runner (`scripted`), runner still alive | 0.44 to 0.45 s | 3 events the runner wrote while the API was down ingested after 0.54 to 0.55 s; the reconciler then followed the runner to its end (3.7 to 3.8 s) | One run, one answer, every file event in the log exactly once, no re-run |
| In-VM runner, runner job killed while the API was down | 0.44 s | `run_interrupted{reason: "runner_lost"}` already in the log when the API answered (0.44 s) | The events written before the kill are kept; the message is re-run once; one answer |
| Host loop (`deepagents_ollama`, Gemma 4) | 0.44 to 0.45 s | `run_interrupted{reason: "api_restart"}` already in the log when the API answered (0.44 to 0.45 s) | The message is re-run once on the transcript from the log, against the same VM; one answer |

The reconciliation itself (fold, reattach the VM, close the run) took 13 to 17 ms per session when no runner had to be followed, from the API's own log line. The poll interval of the tests is 0.1 s, so the times above have that resolution.

Two microsandbox behaviours found while building this (0.7.15):

| Question | Result |
|---|---|
| Does a process started with `exec_stream` survive a `kill -9` of the host process that started it? | **No.** It stops at once (a loop writing a file every 0.5 s stopped at the kill). A managed job started with `exec_detached` keeps running, its stdin works, `follow_logs()` replays its output from the start and ends when it exits, and `inspect()` reports its state and exit code. The in-VM runner therefore runs as a job. |
| Can `fork()` copy a VM created with `Network.none()`? | **No.** It raises `InvalidConfigError` ("source uses host-backed proxy, TLS or secret resources"). A VM with the default network forks in about 0.3 s (one measurement), and the fork kept running after the process that created it exited. Forks of host-placement sessions are therefore rebuilt from the repo plus the saved workspace patch. |

## The Claude Agent SDK harness in a VM

Measured on 2026-11-16 while building `claude_agent` (microsandbox 0.7.15, `claude-agent-sdk` 0.2.182 with CLI 2.1.325, `deepseek-flash`), one run each unless stated.

| Question | Result |
|---|---|
| SDK installed on the host for Linux arm64 (`uv pip install --target`) and bind-mounted read-only into the VM | Works: `PYTHONPATH` points at the mount, and the bundled CLI runs from it. The install is about 380 MB |
| `permission_mode="bypassPermissions"` as root in the VM | **Refused** by the CLI (`--dangerously-skip-permissions cannot be used with root/sudo privileges`). The runner uses `dontAsk` with an allow-list of the built-in tools instead |
| `fork()` of a VM with secrets | **Refused** (`InvalidConfigError`, same message as for `Network.none()`), so `claude_agent` forks are rebuilt |
| Per-turn usage in the stream | The `message_delta` stream event at the end of each model turn carries that turn's usage (input, cache read and output tokens); the run's totals are in the final `result` message. The budget check adds up the per-turn usage |
| First run in a fresh VM, a one-line answer (`run_finished.usage`) | 10 784 input, 183 output tokens, about $0.0035 at peak prices |
| Next run with the SDK's own session resumed | 235 input, 10 752 cache read, 39 output tokens |
| Budget below one turn (`LAB_MAX_USD_PER_RUN=0.00005`) | `run_failed{reason: "budget"}` after the first turn (10 723 input, 110 output tokens). That turn had issued two tool calls at once: the first (`ls`) still ran, the second was rejected by the interrupt |
