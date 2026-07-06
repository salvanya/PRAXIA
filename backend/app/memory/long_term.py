import logging
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import models

from app.config import get_settings
from app.db import get_pool
from app.embeddings import embed_query
from app.vectorstore import get_client

logger = logging.getLogger(__name__)


def _practice_filter(practice_id: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key="practice_id", match=models.MatchValue(value=practice_id)),
            models.FieldCondition(key="scope", match=models.MatchValue(value="practice")),
        ]
    )


@dataclass
class Neighbor:
    id: str
    content: str
    score: float


@dataclass
class Probe:
    vector: list[float]
    related: list[Neighbor]


async def probe(practice_id: str, content: str) -> Probe:
    """Embebe `content` y devuelve los vecinos practice-scope con score >= contradiction_low.

    SIN techo: la distinción duplicado/contradicción es semántica (el coseno no separa
    'mismo hecho reformulado' de 'mismo sujeto, valor cambiado' — 30→45 min puede dar >=0.9)
    → la decide el juez (reflect.judge_neighbor), no un umbral. Reusa el vector para el store."""
    s = get_settings()
    vector = await embed_query(content)
    result = await get_client().query_points(
        collection_name=s.qdrant_memories_collection,
        query=vector,
        query_filter=_practice_filter(practice_id),
        limit=s.memory_top_k,
        with_payload=True,
    )
    related: list[Neighbor] = []
    for point in result.points:
        if point.score >= s.memory_contradiction_low:
            payload = point.payload or {}
            related.append(
                Neighbor(id=str(point.id), content=payload["content"], score=point.score)
            )
    return Probe(vector=vector, related=related[: s.memory_contradiction_max_candidates])


async def ensure_memories_collection() -> None:
    s = get_settings()
    client = get_client()
    if not await client.collection_exists(s.qdrant_memories_collection):
        await client.create_collection(
            collection_name=s.qdrant_memories_collection,
            vectors_config=models.VectorParams(size=s.embed_dim, distance=models.Distance.COSINE),
        )


async def recall(query: str, practice_id: str) -> list[dict[str, Any]]:
    s = get_settings()
    vector = await embed_query(query)
    result = await get_client().query_points(
        collection_name=s.qdrant_memories_collection,
        query=vector,
        query_filter=_practice_filter(practice_id),
        limit=s.memory_top_k,
        score_threshold=s.memory_min_score,
        with_payload=True,
    )
    out: list[dict[str, Any]] = []
    for point in result.points:
        payload = point.payload or {}
        out.append(
            {
                "id": str(point.id),
                "content": payload["content"],
                "kind": payload.get("kind", "hecho"),
                "scope": payload.get("scope", "practice"),
                "score": point.score,
            }
        )
    return out


async def _top_match(practice_id: str, vector: list[float]) -> tuple[str, float] | None:
    result = await get_client().query_points(
        collection_name=get_settings().qdrant_memories_collection,
        query=vector,
        query_filter=_practice_filter(practice_id),
        limit=1,
        with_payload=False,
    )
    if not result.points:
        return None
    p = result.points[0]
    return str(p.id), p.score


async def touch_last_used(ids: list[str]) -> None:
    if not ids:
        return
    pool = await get_pool()
    await pool.execute("UPDATE memories SET last_used_at = now() WHERE id = ANY($1::uuid[])", ids)


async def forget(practice_id: str, ids: list[str]) -> int:
    """Borra memorias por id (Qdrant PRIMERO, luego PG), ambos lados scoped por practice_id.

    Qdrant primero porque el recall lee el `content` del payload de Qdrant (sin join a PG):
    borrar el vector antes garantiza que un fallo parcial no deje un punto huérfano que el
    recall mostraría como memoria fantasma. Devuelve cuántas filas PG se borraron."""
    if not ids:
        return 0
    s = get_settings()
    await get_client().delete(
        collection_name=s.qdrant_memories_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.HasIdCondition(has_id=list(ids)),
                    models.FieldCondition(
                        key="practice_id", match=models.MatchValue(value=practice_id)
                    ),
                ]
            )
        ),
    )
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM memories WHERE id = ANY($1::uuid[]) AND practice_id = $2",
        list(ids),
        practice_id,
    )
    return int(result.split()[-1]) if result else 0


async def store(
    practice_id: str, *, kind: str, content: str, source: str, salience: float
) -> str | None:
    """Persiste una memoria practice-scope: dedup por coseno → PG (verdad) → Qdrant (vector).

    Devuelve el id, o None si era duplicada
    (score >= memory_dedup_threshold → solo toca la existente)."""
    s = get_settings()
    vector = await embed_query(content)
    match = await _top_match(practice_id, vector)
    if match is not None and match[1] >= s.memory_dedup_threshold:
        await touch_last_used([match[0]])
        return None
    mem_id = str(uuid.uuid4())
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO memories (id, practice_id, scope, kind, content, source, salience)
           VALUES ($1, $2, 'practice', $3, $4, $5, $6)""",
        mem_id,
        practice_id,
        kind,
        content,
        source,
        salience,
    )
    try:
        await get_client().upsert(
            collection_name=s.qdrant_memories_collection,
            points=[
                models.PointStruct(
                    id=mem_id,
                    vector=vector,
                    payload={
                        "practice_id": practice_id,
                        "scope": "practice",
                        "kind": kind,
                        "content": content,
                        "salience": salience,
                    },
                )
            ],
        )
    except Exception:
        # compensación: nunca dejar PG-sin-vector (memoria invisible al recall)
        await pool.execute("DELETE FROM memories WHERE id = $1", mem_id)
        raise
    return mem_id
