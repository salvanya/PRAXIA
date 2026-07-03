import pytest

from app.config import Settings
from app.db import get_pool


def test_memory_settings_defaults() -> None:
    s = Settings()
    assert s.ollama_model_cheap == "gemma4:e4b"
    assert s.qdrant_memories_collection == "praxia_memories"
    assert s.memory_top_k == 5
    assert 0.0 < s.memory_min_score < s.memory_dedup_threshold <= 1.0
    assert s.memory_reflect_max_candidates >= 1


@pytest.mark.integration
async def test_memories_table_exists() -> None:
    pool = await get_pool()
    exists = await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'memories')"
    )
    assert exists, "aplicá backend/app/schema.sql (psql < schema.sql) para crear 'memories'"
