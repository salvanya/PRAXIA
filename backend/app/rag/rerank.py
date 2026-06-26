import asyncio
import logging
import math
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.models import Chunk

logger = logging.getLogger(__name__)


@lru_cache
def _model() -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().rerank_model)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _score(query: str, chunks: list[Chunk]) -> list[float]:
    pairs = [(query, c["text"]) for c in chunks]
    raw = _model().predict(pairs)
    return [_sigmoid(float(s)) for s in raw]


async def rerank(query: str, chunks: list[Chunk]) -> list[Chunk]:
    """Reordena los candidatos por relevancia (cross-encoder), descarta los que
    no superan el floor y corta a top_k. Ante fallo del modelo, degrada al orden
    denso de entrada en vez de romper el turno (CLAUDE.md: rerank siempre, pero
    resiliencia > pureza ante un fallo transitorio de carga)."""
    if not chunks:
        return []
    s = get_settings()
    try:
        scores = await asyncio.to_thread(_score, query, chunks)
    except Exception:
        logger.warning("reranker fallo; fallback al orden denso", exc_info=True)
        return chunks[: s.top_k]
    ranked = sorted(zip(chunks, scores, strict=True), key=lambda cs: cs[1], reverse=True)
    kept = [chunk for chunk, score in ranked if score >= s.rerank_min_score]
    return kept[: s.top_k]
