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


def test_short_term_history_window_default() -> None:
    get_settings.cache_clear()
    assert get_settings().short_term_history_window == 10


def test_pii_settings_defaults() -> None:
    get_settings.cache_clear()
    s = get_settings()
    assert s.pii_redaction_enabled is True
    assert s.pii_spacy_model == "es_core_news_md"
    assert s.pii_score_threshold == 0.5


def test_pii_redaction_enabled_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PII_REDACTION_ENABLED", "false")
    get_settings.cache_clear()
    assert get_settings().pii_redaction_enabled is False


def test_memoria_rica_defaults() -> None:
    from app.config import Settings

    s = Settings()
    assert s.memory_contradiction_enabled is True
    assert s.memory_contradiction_low == 0.6
    assert s.memory_contradiction_max_candidates == 3
    assert s.memory_command_enabled is True
    assert s.memory_forget_min_score == 0.6
