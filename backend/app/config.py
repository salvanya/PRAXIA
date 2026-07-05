from functools import lru_cache
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://praxia:praxia@localhost:5432/praxia"
    qdrant_url: str = "http://localhost:6333"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:12b"
    embed_model: str = "BAAI/bge-m3"
    # TODO(Fase 4): practice_id viene hoy de config (single-tenant en dev).
    # Con auth real debe derivarse del usuario autenticado por request y
    # aplicarse vía RLS; nunca un default global. Ver CLAUDE.md §0.5 y §7.
    practice_id: str = "00000000-0000-0000-0000-000000000001"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    top_k: int = 5
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_min_score: float = 0.2
    rag_fetch_k: int = 20
    rag_max_attempts: int = 2
    sql_row_limit: int = 200
    sql_timeout_ms: int = 5000
    sql_max_attempts: int = 2
    appt_default_duration_min: int = 30
    appt_name_match_limit: int = 5
    short_term_history_window: int = (
        10  # mensajes recientes que ve chitchat (ventana fija; running_summary = Fase 2)
    )
    pii_redaction_enabled: bool = True
    pii_spacy_model: str = "es_core_news_md"
    pii_score_threshold: float = 0.5

    # Memoria de largo plazo (Fase 2 Slice 2)
    ollama_model_cheap: str = "gemma4:e4b"  # gate/extract de reflexión (consolida el literal e4b)
    memory_recall_enabled: bool = True
    memory_reflect_enabled: bool = True
    memory_top_k: int = 5
    memory_min_score: float = 0.5
    memory_dedup_threshold: float = 0.9
    memory_reflect_max_candidates: int = 3
    memory_reflect_timeout_s: float = 10.0

    # Context Manager (Fase 2 Slice 3)
    context_token_budget: int = (
        3000  # tokens aprox del ensamblado de chitchat (holgado bajo num_ctx)
    )
    summary_enabled: bool = True
    summary_timeout_s: float = 8.0  # <= memory_reflect_timeout_s (ventana concurrente)
    summary_max_words: int = 150

    # Constants (not from env)
    qdrant_collection: ClassVar[str] = "praxia_chunks"
    qdrant_memories_collection: ClassVar[str] = "praxia_memories"
    embed_dim: ClassVar[int] = 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
