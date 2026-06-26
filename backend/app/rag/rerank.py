import asyncio
import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.models import Chunk

logger = logging.getLogger(__name__)


@lru_cache
def _model() -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().rerank_model)


def _score(query: str, chunks: list[Chunk]) -> list[float]:
    # CrossEncoder(bge-reranker-v2-m3) ya aplica Sigmoid por defecto
    # (default_activation_function=Sigmoid): predict() devuelve probabilidades de
    # relevancia en [0,1]. NO re-sigmoidear (comprimiria todo a [0.5, 0.73] y
    # anularia el floor). Verificado con el modelo real: relevante ~0.999,
    # irrelevante ~1.6e-5.
    pairs = [(query, c["text"]) for c in chunks]
    return [float(s) for s in _model().predict(pairs)]


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
