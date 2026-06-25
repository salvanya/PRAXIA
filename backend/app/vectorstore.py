import uuid

from qdrant_client import AsyncQdrantClient, models

from app.config import get_settings
from app.models import Chunk

_client: AsyncQdrantClient | None = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=get_settings().qdrant_url)
    return _client


async def ensure_collection() -> None:
    s = get_settings()
    client = _get_client()
    if not await client.collection_exists(s.qdrant_collection):
        await client.create_collection(
            collection_name=s.qdrant_collection,
            vectors_config=models.VectorParams(size=s.embed_dim, distance=models.Distance.COSINE),
        )


async def upsert_chunks(chunks: list[Chunk], vectors: list[list[float]], practice_id: str) -> None:
    s = get_settings()
    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={**chunk, "practice_id": practice_id},
        )
        for chunk, vec in zip(chunks, vectors, strict=True)
    ]
    await _get_client().upsert(collection_name=s.qdrant_collection, points=points)


async def count_chunks(document_id: str, practice_id: str) -> int:
    s = get_settings()
    result = await _get_client().count(
        collection_name=s.qdrant_collection,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document_id)
                ),
                models.FieldCondition(
                    key="practice_id", match=models.MatchValue(value=practice_id)
                ),
            ]
        ),
        exact=True,
    )
    return result.count


async def delete_document(document_id: str, practice_id: str) -> None:
    s = get_settings()
    await _get_client().delete(
        collection_name=s.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=document_id)
                    ),
                    models.FieldCondition(
                        key="practice_id", match=models.MatchValue(value=practice_id)
                    ),
                ]
            )
        ),
    )


async def search(vector: list[float], practice_id: str, top_k: int) -> list[Chunk]:
    s = get_settings()
    result = await _get_client().query_points(
        collection_name=s.qdrant_collection,
        query=vector,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key="practice_id", match=models.MatchValue(value=practice_id))
            ]
        ),
        limit=top_k,
        with_payload=True,
    )
    out: list[Chunk] = []
    for point in result.points:
        p = point.payload or {}
        out.append(
            Chunk(
                text=p["text"],
                page=p.get("page"),
                chunk_index=p["chunk_index"],
                document_id=p["document_id"],
                title=p["title"],
                doc_type=p["doc_type"],
            )
        )
    return out
