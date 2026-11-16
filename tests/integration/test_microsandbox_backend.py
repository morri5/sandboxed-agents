"""`MicrosandboxBackend` against a real VM: the file tools deepagents derives from it."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from microsandbox import Sandbox

from lab.harnesses.microsandbox_backend import MicrosandboxBackend
from lab.sandbox import REPO_DIR

pytestmark = pytest.mark.integration

CALC = f"{REPO_DIR}/calc/__init__.py"


@pytest.fixture
async def backend(host_vm: Sandbox) -> MicrosandboxBackend:
    return MicrosandboxBackend(host_vm, await host_vm.name)


async def test_vm_has_no_network(
    host_vm: Sandbox, assert_no_network: Callable[[Sandbox], Awaitable[None]]
) -> None:
    await assert_no_network(host_vm)


async def test_execute_runs_in_the_clone(backend: MicrosandboxBackend) -> None:
    result = await backend.aexecute("pwd && git log --oneline && echo oops >&2 && exit 3")
    assert result.exit_code == 3
    lines = result.output.splitlines()
    assert lines[0] == REPO_DIR
    assert lines[1].endswith(" initial")  # a fixture becomes a single-commit repo
    assert lines[-1] == "[stderr] oops"


async def test_execute_timeout(backend: MicrosandboxBackend) -> None:
    result = await backend.aexecute("sleep 10", timeout=1)
    assert result.exit_code == 124
    assert "timed out" in result.output


async def test_ls(backend: MicrosandboxBackend) -> None:
    result = await backend.als(REPO_DIR)
    assert result.error is None and result.entries is not None
    entries = {e["path"]: e["is_dir"] for e in result.entries}
    assert entries[f"{REPO_DIR}/calc"] is True
    assert entries[f"{REPO_DIR}/README.md"] is False


async def test_read(backend: MicrosandboxBackend) -> None:
    result = await backend.aread(CALC, offset=0, limit=5)
    assert result.error is None and result.file_data is not None
    assert result.file_data["content"].startswith('"""Small arithmetic helpers."""')
    assert result.end_line == 5 and result.total_lines == 16

    missing = await backend.aread(f"{REPO_DIR}/nope.py")
    assert missing.error is not None


async def test_write_creates_parents_and_read_back(backend: MicrosandboxBackend) -> None:
    path = f"{REPO_DIR}/new/dir/notes.txt"
    assert (await backend.awrite(path, "one\ntwo\n")).error is None
    read = await backend.aread(path)
    assert read.file_data is not None and read.file_data["content"] == "one\ntwo"
    [download] = await backend.adownload_files([path])
    assert download.content == b"one\ntwo\n"


async def test_edit(backend: MicrosandboxBackend) -> None:
    result = await backend.aedit(CALC, "sum(values) // len(values)", "sum(values) / len(values)")
    assert result.error is None and result.occurrences == 1
    out = await backend.aexecute("python -m unittest 2>&1 | tail -1")
    assert out.output == "OK"

    missing = await backend.aedit(CALC, "not in the file", "x")
    assert missing.error is not None

    big = "x" * 300_000  # above the inline limit: goes through upload_files
    assert (await backend.aedit(CALC, '"""Small arithmetic helpers."""', big)).error is None
    assert (await backend.aexecute(f"grep -c {big[:50]} calc/__init__.py")).output == "1"


async def test_grep(backend: MicrosandboxBackend) -> None:
    result = await backend.agrep("def average", REPO_DIR)
    assert result.error is None and result.matches is not None
    assert [(m["path"], m["line"]) for m in result.matches] == [(CALC, 12)]

    filtered = await backend.agrep("import", REPO_DIR, glob="test_*.py")
    assert filtered.matches is not None
    assert {m["path"] for m in filtered.matches} == {f"{REPO_DIR}/tests/test_calc.py"}


async def test_glob(backend: MicrosandboxBackend) -> None:
    result = await backend.aglob("**/*.py", REPO_DIR)
    assert result.matches is not None
    assert {m["path"] for m in result.matches} == {
        CALC,
        f"{REPO_DIR}/tests/__init__.py",
        f"{REPO_DIR}/tests/test_calc.py",
    }


async def test_download_missing_file(backend: MicrosandboxBackend) -> None:
    [response] = await backend.adownload_files([f"{REPO_DIR}/nope"])
    assert response.content is None and response.error == "file_not_found"


async def test_sync_methods_from_a_worker_thread(backend: MicrosandboxBackend) -> None:
    result = await asyncio.to_thread(backend.read, CALC)
    assert result.file_data is not None and "def add" in result.file_data["content"]
    with pytest.raises(RuntimeError, match="event loop"):
        backend.execute("true")  # on the loop thread it would deadlock, so it refuses
