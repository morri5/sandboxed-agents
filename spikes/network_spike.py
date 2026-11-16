"""Spike: network isolation and the host-scoped DeepSeek secret.

1. Tool-only VM (`Network.none()`): the internet is unreachable.
2. In-VM agent VM (DNS plus `api.deepseek.com` only): example.com is unreachable,
   DeepSeek answers, the guest env holds only a placeholder for the key, one
   minimal Messages call works, and the Agent SDK's bundled CLI completes a small
   task that uses tools, with token usage reported per turn.
3. Optional B-local probes: reach the host's Ollama (which stays on 127.0.0.1)
   through a host Unix socket bridged over vsock, and through a `host` rule for
   port 11434 only.

The real key is read from DEEPSEEK_API_KEY on the host and handed only to the
microsandbox secret proxy. Everything printed from the guest is redacted.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from _common import IMAGE, OUT, cleanup, ensure_home, ms, write_result
from microsandbox import (
    DestGroup,
    Destination,
    Network,
    NetworkPolicy,
    Protocol,
    Rule,
    Sandbox,
    Secret,
    SecretSubstitution,
)

PREFIX = "lab-spike-net-"
DEEPSEEK_HOST = "api.deepseek.com"
DEEPSEEK_BASE_URL = f"https://{DEEPSEEK_HOST}/anthropic"
SDK_VERSION = "0.2.182"
OLLAMA = ("127.0.0.1", 11434)
OLLAMA_VSOCK_PORT = 11434
PROBE = Path(__file__).parent / "guest_probe.py"

KEY = os.environ.get("DEEPSEEK_API_KEY", "")


def redact(text: str) -> str:
    return text.replace(KEY, "<REDACTED-REAL-KEY>") if KEY else text


async def probe(sb: Sandbox, *args: str) -> tuple[bool, dict[str, Any]]:
    """Run the guest probe. True means the request reached the upstream service.

    A connection error, or the policy proxy's own 403 `network_policy_denied`
    answer, means it did not.
    """
    out = await sb.exec("python3", ["/opt/guest_probe.py", *args], timeout=60)
    text = redact(out.stdout_text.strip() or out.stderr_text.strip())
    try:
        parsed: dict[str, Any] = json.loads(text.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        parsed = {"raw": text[-2000:]}
    denied = "network_policy_denied" in str(parsed.get("body", ""))
    return out.exit_code == 0 and not denied, parsed


async def new_vm(name: str, **kwargs: Any) -> Sandbox:
    sb = await Sandbox.create(f"{PREFIX}{name}", image=IMAGE, replace=True, **kwargs)
    await sb.fs.copy_from_host(str(PROBE), "/opt/guest_probe.py")
    return sb


async def check_no_network() -> dict[str, Any]:
    sb = await new_vm("none", network=Network.none())
    ok_http, http = await probe(sb, "get", "http://example.com")
    ok_https, https = await probe(sb, "get", "https://example.com")
    await sb.destroy(force=True)
    result = {"example_com_http_blocked": not ok_http, "example_com_https_blocked": not ok_https}
    print(f"Network.none(): {result} ({http}, {https})")
    assert not ok_http and not ok_https, "Network.none() let traffic through"
    return result


def bundled_cli() -> Path:
    """Extract the Agent SDK's bundled Linux aarch64 CLI on the host (cached)."""
    cache = OUT / "claude-cli"
    binary = cache / "claude"
    if binary.exists():
        return binary
    wheels = cache / "wheels"
    subprocess.run(
        [
            "uvx", "--from", "pip", "pip", "download", f"claude-agent-sdk=={SDK_VERSION}",
            "--no-deps", "--only-binary=:all:", "--platform", "manylinux_2_17_aarch64",
            "--python-version", "3.13", "-q", "-d", str(wheels),
        ],
        check=True,
    )  # fmt: skip
    wheel = next(wheels.glob("claude_agent_sdk-*.whl"))
    with zipfile.ZipFile(wheel) as zf, zf.open("claude_agent_sdk/_bundled/claude") as src:
        with binary.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    shutil.rmtree(wheels)
    return binary


def summarize_stream(stdout: str) -> dict[str, Any]:
    """Pull per-turn usage and tool calls out of the CLI's stream-json output."""
    turns, tools, result = [], [], {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            message = event["message"]
            usage = message.get("usage") or {}
            turns.append(
                {
                    "model": message.get("model"),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                }
            )
            tools += [b["name"] for b in message.get("content", []) if b.get("type") == "tool_use"]
        elif event.get("type") == "result":
            result = {
                k: event.get(k)
                for k in ("subtype", "is_error", "num_turns", "usage", "total_cost_usd")
            }
    return {"assistant_turns": turns, "tool_calls": tools, "result": result}


async def check_deepseek_vm(model: str) -> dict[str, Any]:
    substitution = SecretSubstitution(header_fields=("authorization", "x-api-key"))
    secrets = [
        Secret.env(var, value=KEY, allow=[DEEPSEEK_HOST], substitution=substitution)
        for var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
    ]
    network = Network(
        policy=NetworkPolicy(
            rules=(*Rule.allow_dns(), Rule.allow(destination=Destination.domain(DEEPSEEK_HOST)))
        )
    )
    sb = await new_vm(
        "deepseek",
        memory=1024,
        network=network,
        secrets=secrets,
        env={"ANTHROPIC_BASE_URL": DEEPSEEK_BASE_URL},
    )
    result: dict[str, Any] = {}

    env_out = await sb.exec("env")
    placeholders = await sb.shell('printf "%s\\n%s" "$ANTHROPIC_AUTH_TOKEN" "$ANTHROPIC_API_KEY"')
    result["real_key_in_guest_env"] = KEY in env_out.stdout_text
    result["guest_placeholders"] = redact(placeholders.stdout_text).splitlines()
    assert not result["real_key_in_guest_env"], "the real key is visible inside the VM"
    print(f"placeholders in guest: {result['guest_placeholders']}")

    ok, example = await probe(sb, "get", "https://example.com")
    result["example_com_blocked"] = not ok
    print(f"example.com from B VM: blocked={not ok} {example}")
    assert not ok, "example.com reachable from the DeepSeek-only VM"

    ok, models = await probe(
        sb, "get", f"https://{DEEPSEEK_HOST}/models", "--bearer-env", "ANTHROPIC_AUTH_TOKEN"
    )
    status = models.get("status")
    model_ids = [m["id"] for m in json.loads(models["body"])["data"]] if status == 200 else models
    result["deepseek_models_status"] = status
    result["deepseek_models"] = model_ids
    print(f"GET /models through the secret proxy: {status} {model_ids}")
    assert status == 200, "DeepSeek did not answer with the substituted key"
    if model not in model_ids:
        sys.exit(f"model {model!r} not offered by DeepSeek; set LAB_DEEPSEEK_MODEL to one of above")

    ok, messages = await probe(sb, "messages", DEEPSEEK_BASE_URL, model)
    body = json.loads(messages.get("body", "{}") or "{}")
    result["messages_call"] = {
        "status": messages.get("status"),
        "text": "".join(b.get("text", "") for b in body.get("content", [])),
        "usage": body.get("usage"),
    }
    print(f"Messages API call: {result['messages_call']}")
    assert messages.get("status") == 200, messages

    t0 = time.perf_counter()
    await sb.fs.copy_from_host(str(bundled_cli()), "/usr/local/bin/claude")
    await sb.exec("chmod", ["+x", "/usr/local/bin/claude"])
    result["cli_copy_ms"] = ms(time.perf_counter() - t0)
    version = await sb.exec("claude", ["--version"], timeout=60)
    result["cli_version"] = version.stdout_text.strip()

    await sb.exec("mkdir", ["-p", "/work"])
    t0 = time.perf_counter()
    cli = await sb.exec(
        "claude",
        [
            "-p", "Create a file hello.txt containing exactly: ok. Then run: cat hello.txt",
            "--output-format", "stream-json", "--verbose",
            "--allowedTools", "Write,Read,Bash(cat:*)",
            "--max-turns", "6",
        ],
        cwd="/work",
        env={
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        },
        timeout=300,
    )  # fmt: skip
    hello = await sb.exec("cat", ["/work/hello.txt"])
    result["cli_task"] = {
        "exit_code": cli.exit_code,
        "wall_ms": ms(time.perf_counter() - t0),
        "hello_txt": hello.stdout_text.strip() if hello.exit_code == 0 else None,
        **summarize_stream(redact(cli.stdout_text)),
    }
    if cli.exit_code != 0:
        result["cli_task"]["stderr_tail"] = redact(cli.stderr_text[-2000:])
    print(f"CLI task: {json.dumps(result['cli_task'], indent=2)}")
    await sb.destroy(force=True)
    assert cli.exit_code == 0 and result["cli_task"]["hello_txt"] == "ok", "CLI task failed"
    return result


async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Forward one host Unix socket connection to Ollama on 127.0.0.1."""
    up_reader, up_writer = await asyncio.open_connection(*OLLAMA)

    async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
        dst.close()

    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


def ollama_reply(ok: bool, reply: dict[str, Any]) -> dict[str, Any]:
    if not ok:
        return {"works": False, "error": reply}
    body = reply.get("body", "")
    return {"works": "models" in body, "status_line": reply.get("status_line")}


async def check_ollama_paths() -> dict[str, Any]:
    result: dict[str, Any] = {}

    socket_path = OUT / "ollama.sock"
    socket_path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(bridge, path=str(socket_path))
    try:
        sb = await new_vm(
            "ollama-vsock",
            network=Network.none(),
            vsock={str(socket_path): OLLAMA_VSOCK_PORT},
        )
        result["vsock_bridge"] = ollama_reply(*await probe(sb, "vsock", str(OLLAMA_VSOCK_PORT)))
        await sb.destroy(force=True)
    except Exception as exc:  # optional probe: record, do not fail the spike
        result["vsock_bridge"] = {"works": False, "error": repr(exc)}
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)
    print(f"Ollama via host socket bridge: {result['vsock_bridge']}")

    host_rule = Rule.allow(
        destination=Destination.group(DestGroup.HOST), protocol=Protocol.TCP, port=11434
    )
    try:
        sb = await new_vm(
            "ollama-host",
            network=Network(policy=NetworkPolicy(rules=(*Rule.allow_dns(), host_rule))),
        )
        reply = await probe(sb, "tcp", "host.microsandbox.internal", "11434")
        result["host_rule_11434"] = ollama_reply(*reply)
        await sb.destroy(force=True)
    except Exception as exc:  # optional probe: record, do not fail the spike
        result["host_rule_11434"] = {"works": False, "error": repr(exc)}
    print(f"Ollama via host rule for port 11434: {result['host_rule_11434']}")
    return result


async def main() -> None:
    if not KEY:
        sys.exit("DEEPSEEK_API_KEY is not set on the host")
    model = os.environ.get("LAB_DEEPSEEK_MODEL", "deepseek-flash")
    ensure_home()
    await cleanup(PREFIX)
    try:
        result: dict[str, Any] = {"model": model}
        result["no_network_vm"] = await check_no_network()
        result["deepseek_vm"] = await check_deepseek_vm(model)
        result["b_local_ollama"] = await check_ollama_paths()
    finally:
        await cleanup(PREFIX)
    write_result("network_spike", result)


if __name__ == "__main__":
    asyncio.run(main())
