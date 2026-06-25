import os

import asyncpg
import httpx
import pytest


@pytest.mark.integration
async def test_postgres_reachable():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    value = await conn.fetchval("SELECT 1")
    await conn.close()
    assert value == 1


@pytest.mark.integration
def test_qdrant_reachable():
    resp = httpx.get(os.environ["QDRANT_URL"] + "/readyz", timeout=5)
    assert resp.status_code == 200
