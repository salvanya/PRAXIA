import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embed_model)


def _validate_dim(vectors: list[list[float]]) -> None:
    """Guarda del gotcha 1024: la dim del embedding debe coincidir con la
    colección Qdrant (settings.embed_dim), o el upsert falla silenciosamente."""
    if not vectors:
        return
    expected = get_settings().embed_dim
    actual = len(vectors[0])
    if actual != expected:
        raise ValueError(
            f"Dimensión de embedding {actual} != embed_dim configurado ({expected}). "
            "Alineá embed_model con la colección Qdrant (gotcha bge-m3 = 1024 dims)."
        )


def _encode(texts: list[str]) -> list[list[float]]:
    arr = _model().encode(texts, normalize_embeddings=True)
    vectors = [row.tolist() for row in arr]
    _validate_dim(vectors)
    return vectors


async def embed_texts(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(_encode, texts)


async def embed_query(text: str) -> list[float]:
    vecs = await asyncio.to_thread(_encode, [text])
    return vecs[0]
