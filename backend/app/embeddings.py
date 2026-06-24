import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embed_model)


def _encode(texts: list[str]) -> list[list[float]]:
    arr = _model().encode(texts, normalize_embeddings=True)
    return [row.tolist() for row in arr]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(_encode, texts)


async def embed_query(text: str) -> list[float]:
    vecs = await asyncio.to_thread(_encode, [text])
    return vecs[0]
