import pytest

from app import vectorstore
from app.models import Chunk


def _chunk(text: str, idx: int, doc: str) -> Chunk:
    return Chunk(text=text, page=1, chunk_index=idx, document_id=doc,
                 title="Protocolo", doc_type="protocolo")


@pytest.mark.integration
async def test_search_filters_by_practice():
    await vectorstore.ensure_collection()
    v_a = [1.0] + [0.0] * 1023
    v_b = [0.0, 1.0] + [0.0] * 1022
    await vectorstore.upsert_chunks([_chunk("texto practica A", 0, "doc-a")], [v_a], "practice-A")
    await vectorstore.upsert_chunks([_chunk("texto practica B", 0, "doc-b")], [v_b], "practice-B")

    hits = await vectorstore.search(v_a, practice_id="practice-A", top_k=5)
    assert hits, "should retrieve A's chunk"
    assert all(h["document_id"] != "doc-b" for h in hits), "must not leak practice B"
