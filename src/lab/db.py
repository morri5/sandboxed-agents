"""Database connection settings, the asyncpg pool and the migration runner.

`python -m lab.db` applies every pending file in `migrations/` (used by `make migrate`).
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import asyncpg

DATABASE_URL = os.environ.get("LAB_DATABASE_URL", "postgresql://lab:lab@127.0.0.1:55432/lab")
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


async def _init_connection(conn: Any) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str = DATABASE_URL) -> Any:
    return await asyncpg.create_pool(dsn, init=_init_connection)


async def migrate(dsn: str = DATABASE_URL) -> list[str]:
    """Apply pending migrations in file name order, each in its own transaction."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        applied = []
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
            applied.append(path.name)
        return applied
    finally:
        await conn.close()


if __name__ == "__main__":
    print("applied:", asyncio.run(migrate()) or "nothing, already up to date")
