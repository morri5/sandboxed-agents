"""deepagents backend whose every file and shell operation runs in a microsandbox VM.

deepagents derives ls, read, write, edit, glob and grep from `execute()` plus
file upload and download, so this class only maps those three onto the VM:
`execute` is one `sb.exec("sh", ["-c", command])` in the clone, uploads and
downloads use `sb.fs`. The agent loop stays on the host; the VM is only its hands.

microsandbox is async, so the async methods are the real implementations. The
sync ones (required by the base class) hop onto the event loop the backend was
created on, and must therefore be called from another thread.
"""

import asyncio
from collections.abc import Coroutine
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from microsandbox import ExecTimeoutError, MicrosandboxError, PathNotFoundError, Sandbox

from lab import config
from lab.sandbox import REPO_DIR


class MicrosandboxBackend(BaseSandbox):
    def __init__(
        self,
        sandbox: Sandbox,
        name: str,
        cwd: str = REPO_DIR,
        timeout: int = config.EXEC_TIMEOUT_S,
    ) -> None:
        self._sb = sandbox
        self._name = name  # `Sandbox.name` is awaitable, a property here cannot await it
        self._cwd = cwd
        self._timeout = timeout
        self._loop = asyncio.get_running_loop()

    @property
    def id(self) -> str:
        return self._name

    def _sync[T](self, coro: Coroutine[Any, Any, T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result()
        coro.close()
        raise RuntimeError("sync backend method called on the event loop; use the async one")

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        limit = timeout or self._timeout
        try:
            out = await self._sb.exec("sh", ["-c", command], cwd=self._cwd, timeout=limit)
        except ExecTimeoutError:
            return ExecuteResponse(
                output=f"Error: command timed out after {limit} s", exit_code=124
            )
        # Same shape as deepagents' local shell backend: stderr lines tagged.
        parts = [out.stdout_text.rstrip("\n")] if out.stdout_text else []
        parts += [f"[stderr] {line}" for line in out.stderr_text.strip().splitlines()]
        return ExecuteResponse(output="\n".join(parts), exit_code=out.exit_code)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._sync(self.aexecute(command, timeout=timeout))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        parents = sorted({str(PurePosixPath(path).parent) for path, _ in files})
        await self._sb.exec("mkdir", ["-p", *parents])
        responses = []
        for path, content in files:
            try:
                await self._sb.fs.write(path, content)
                responses.append(FileUploadResponse(path=path))
            except MicrosandboxError as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._sync(self.aupload_files(files))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = []
        for path in paths:
            try:
                responses.append(
                    FileDownloadResponse(path=path, content=await self._sb.fs.read(path))
                )
            except MicrosandboxError as exc:
                # A missing file arrives as a generic fs error, not PathNotFoundError.
                missing = isinstance(exc, PathNotFoundError) or "No such file" in str(exc)
                error = "file_not_found" if missing else str(exc)
                responses.append(FileDownloadResponse(path=path, error=error))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._sync(self.adownload_files(paths))
