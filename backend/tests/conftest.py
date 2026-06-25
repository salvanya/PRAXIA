import os
from pathlib import Path

import pytest

from app import db, vectorstore
from app.config import get_settings

# Load .env into the environment for tests if present.
env_path = Path(__file__).resolve().parents[2] / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


@pytest.fixture(autouse=True)
async def _reset_async_singletons():
    """Each test runs in its own event loop (pytest-asyncio auto mode); the
    module-level asyncpg pool / Qdrant client are loop-bound, so close and
    reset them after every test to avoid cross-loop reuse errors.

    Also clears the get_settings lru_cache so that any monkeypatched env vars
    in one test (e.g. test_config) do not bleed a stale Settings object into
    subsequent tests."""
    yield
    if db._pool is not None:
        await db._pool.close()
        db._pool = None
    if vectorstore._client is not None:
        await vectorstore._client.close()
        vectorstore._client = None
    get_settings.cache_clear()
