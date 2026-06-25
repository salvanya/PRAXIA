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
    practice_id: str = "00000000-0000-0000-0000-000000000001"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    top_k: int = 5

    # Constants (not from env)
    qdrant_collection: ClassVar[str] = "praxia_chunks"
    embed_dim: ClassVar[int] = 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
