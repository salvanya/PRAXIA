from app import embeddings, vectorstore
from app.config import get_settings
from app.models import Chunk


async def retrieve(
    query: str, practice_id: str | None = None, top_k: int | None = None
) -> list[Chunk]:
    s = get_settings()
    vector = await embeddings.embed_query(query)
    return await vectorstore.search(
        vector,
        practice_id=practice_id if practice_id is not None else s.practice_id,
        top_k=top_k if top_k is not None else s.top_k,
    )
