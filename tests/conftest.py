"""Tests use their own `lab_test` database on the compose Postgres (`make db-up`)."""

import asyncio
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from lab.db import DATABASE_URL, create_pool, migrate

TEST_DATABASE_URL = os.environ.get(
    "LAB_TEST_DATABASE_URL", DATABASE_URL.rsplit("/", 1)[0] + "/lab_test"
)


async def _create_test_database() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'lab_test'"):
            await conn.execute("CREATE DATABASE lab_test")
    finally:
        await conn.close()
    await migrate(TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def test_dsn() -> str:
    asyncio.run(_create_test_database())
    return TEST_DATABASE_URL


@pytest.fixture
async def pool(test_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(test_dsn)
    await pool.execute("TRUNCATE events")
    yield pool
    await pool.close()
