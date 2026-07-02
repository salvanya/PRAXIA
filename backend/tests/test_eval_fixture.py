import pytest

from app import vectorstore
from app.config import get_settings
from app.eval.fixtures import ensure_rag_fixture
from app.rag.retrieve import retrieve


@pytest.mark.eval
@pytest.mark.llm
async def test_gate_self_heals_after_qdrant_wipe() -> None:
    """Reproduce el bug: la suite fast wipea Qdrant. ensure_rag_fixture debe re-sembrar
    el corpus y dejarlo recuperable."""
    s = get_settings()
    client = vectorstore._get_client()
    if await client.collection_exists(s.qdrant_collection):
        await client.delete_collection(s.qdrant_collection)  # simula el wipe de test_vectorstore
    await ensure_rag_fixture()
    chunks = await retrieve("¿cuánto dura la primera consulta?", top_k=s.rag_fetch_k)
    assert any(
        "60" in c["text"] for c in chunks
    ), "el fixture RAG no quedó recuperable tras el wipe"
