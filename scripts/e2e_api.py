"""Start the API for the web page's Playwright test (`make web-test`), on the test database.

Usage: python scripts/e2e_api.py PORT

Like the integration tests' `api` fixture: the `lab_test` database is created if missing,
migrated and emptied, so the API's startup recovery finds no other test's sessions.
"""

import asyncio
import os
import sys

import asyncpg

from lab.db import DATABASE_URL, migrate

TEST_DATABASE_URL = os.environ.get(
    "LAB_TEST_DATABASE_URL", DATABASE_URL.rsplit("/", 1)[0] + "/lab_test"
)


async def prepare() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'lab_test'"):
            await conn.execute("CREATE DATABASE lab_test")
    finally:
        await conn.close()
    await migrate(TEST_DATABASE_URL)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("TRUNCATE events")
    finally:
        await conn.close()


if __name__ == "__main__":
    port = sys.argv[1]
    asyncio.run(prepare())
    os.environ["LAB_DATABASE_URL"] = TEST_DATABASE_URL
    os.execv(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "lab.api:app", "--port", port, "--log-level", "warning"],
    )
