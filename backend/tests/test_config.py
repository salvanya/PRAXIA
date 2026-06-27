from pytest import MonkeyPatch

from app.config import get_settings


def test_settings_load_from_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:12b")
    monkeypatch.setenv("PRACTICE_ID", "00000000-0000-0000-0000-000000000001")
    get_settings.cache_clear()
    s = get_settings()
    assert s.ollama_model == "gemma4:12b"
    assert s.embed_model == "BAAI/bge-m3"
    assert s.qdrant_collection == "praxia_chunks"
    assert s.embed_dim == 1024
    assert s.top_k == 5


def test_settings_have_sql_defaults() -> None:
    get_settings.cache_clear()
    s = get_settings()
    assert s.sql_row_limit == 200
    assert s.sql_timeout_ms == 5000
    assert s.sql_max_attempts == 2
