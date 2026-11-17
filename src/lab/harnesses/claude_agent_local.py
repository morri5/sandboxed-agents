"""B-local: the Claude Agent SDK inside the VM, the host's Ollama as the brain.

The same in-VM harness as `claude_agent` (the runner drives a `ClaudeSDKClient` as
a managed job), with the brain swapped for the model the host-loop harness uses,
so A vs B-local compares harnesses on one brain. It costs nothing, and no key of
any kind enters the VM.

Ollama has no auth and stays bound to 127.0.0.1. The VM has no network at all
(`Network.none()`: no DNS, no IP traffic). Its only way out is one vsock port,
which microsandbox connects to a Unix socket on the host. Behind that socket,
`OllamaBridge` (in the API process) passes `POST /v1/messages` to Ollama and
refuses everything else, so a prompt-injected agent cannot pull, push, create
or delete models. Inside the VM, the runner listens on a loopback port and
relays each connection to the vsock port, because the CLI only speaks TCP.

Trade-off: the bridge lives in the API process (started with it, through the
registry). While the API is down an in-VM run has no brain; once the API is back
it is served again at the same socket path, which the VM reaches unchanged.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from microsandbox import Network, Sandbox, Volume

from lab import config
from lab.events import AgentEvent, Message
from lab.harnesses.claude_agent import SDK_GUEST, SDK_HOST, SYSTEM_PROMPT, VM_MEMORY_MIB
from lab.sandbox import SandboxError, VmSpec, exec_runner

log = logging.getLogger(__name__)

# Fixed, so a VM reattached after an API restart finds the new bridge at the same path.
# Unix socket paths are limited to 104 bytes on macOS.
BRIDGE_SOCKET = Path.home() / ".microsandbox" / "lab-ollama.sock"
VSOCK_PORT = 11434
ALLOWED = {("POST", "/v1/messages")}  # the Anthropic Messages API, nothing of Ollama's own
MAX_BODY = 32 * 1024 * 1024
OLLAMA_CONTEXT = 131072  # what Ollama serves the default model with (`ollama ps`)
FREE = {"input": 0.0, "cache_read": 0.0, "output": 0.0}


class OllamaBridge:
    """A Unix socket server that relays allowed HTTP requests to Ollama on 127.0.0.1.

    One request per connection: the request head is checked, the body is read by
    its `Content-Length`, and the request goes to Ollama with `Connection: close`;
    nothing else the client sends is forwarded. The answer (a stream for SSE) is
    relayed as it comes.
    """

    def __init__(self, path: Path = BRIDGE_SOCKET) -> None:
        self.path = path
        self.server: asyncio.Server | None = None
        self.inode = 0

    async def start(self) -> None:
        if self.server is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Taken over from a process that died (or another lab API: it serves the same).
        self.path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self._serve, path=str(self.path))
        self.inode = self.path.stat().st_ino

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        self.server = None
        with contextlib.suppress(FileNotFoundError):
            if self.path.stat().st_ino == self.inode:  # not taken over by another process
                self.path.unlink()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._relay(reader, writer)
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass  # a broken or malformed request: just close it
        finally:
            writer.close()

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        request_line, *lines = head.decode("latin-1").split("\r\n")[:-2]
        method, target, version = request_line.split(" ")
        headers = {}
        for line in lines:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if (method, target.partition("?")[0]) not in ALLOWED:
            log.warning("ollama bridge: refused %s %s", method, target[:200])
            return await _answer(writer, 403, "forbidden")
        if "transfer-encoding" in headers:
            return await _answer(writer, 411, "length required")
        length = int(headers.get("content-length", "0"))
        if not 0 <= length <= MAX_BODY:
            return await _answer(writer, 413, "too large")
        body = await reader.readexactly(length)
        host, port = config.OLLAMA_URL.split("//")[1].split(":")
        try:
            up_reader, up_writer = await asyncio.open_connection(host, int(port))
        except OSError:
            return await _answer(writer, 502, "ollama unreachable")
        hop = {"host", "connection", "keep-alive", "proxy-connection", "upgrade"}
        kept = [f"{n}: {v}" for n, v in headers.items() if n not in hop]
        out = [request_line, *kept, f"Host: {host}:{port}", "Connection: close"]
        up_writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + body)
        try:
            await up_writer.drain()
            while data := await up_reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            up_writer.close()


async def _answer(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
    )
    with contextlib.suppress(OSError):
        await writer.drain()


BRIDGE = OllamaBridge()


class ClaudeAgentLocalHarness:
    name = "claude_agent_local"
    placement: Literal["host", "sandbox"] = "sandbox"
    capabilities = frozenset({"streaming", "tool_use", "native_resume", "cost_report"})
    model = config.OLLAMA_MODEL

    def vm_spec(self) -> VmSpec:
        if not SDK_HOST.is_dir():
            raise SandboxError(f"no Claude Agent SDK in {SDK_HOST}: run make claude-sdk")
        return VmSpec(
            network=Network.none(),
            memory_mib=VM_MEMORY_MIB,
            env={
                # A dummy key makes the CLI skip its login; Ollama ignores it.
                "ANTHROPIC_API_KEY": "ollama",
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(OLLAMA_CONTEXT),
                "PYTHONPATH": SDK_GUEST,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1",
            },
            volumes={SDK_GUEST: Volume.bind(str(SDK_HOST), readonly=True)},
            vsock={str(BRIDGE.path): VSOCK_PORT},
        )

    async def run(
        self, transcript: list[Message], sandbox: Sandbox, run_id: str, resume: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        task = {
            "run_id": run_id,
            "harness": self.name,
            "transcript": [asdict(m) for m in transcript],
            "resume": resume,
            "system_prompt": SYSTEM_PROMPT,
            "model": config.OLLAMA_MODEL,
            "price": FREE,
            "budget_usd": None,  # free: no budget capability
            "max_turns": config.MAX_TOOL_CALLS,
            "vsock_port": VSOCK_PORT,
        }
        async for event in exec_runner(sandbox, task):
            yield event
