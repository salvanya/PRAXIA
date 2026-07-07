import pytest

from app.graph import rag_subgraph
from app.models import Chunk

pytestmark = pytest.mark.llm


def _chunk(text: str) -> Chunk:
    return Chunk(
        text=text, page=1, chunk_index=0, document_id="d1", title="Protocolo", doc_type="protocolo"
    )


async def test_memory_only_does_not_abstain(monkeypatch):
    """Regresión del bug: docs vacíos + memoria relevante ⇒ NO abstiene (jueces+síntesis reales)."""

    async def fake_retrieve(query, practice_id=None, top_k=None):
        return []

    async def fake_rerank(query, chunks):
        return chunks

    monkeypatch.setattr(rag_subgraph, "retrieve", fake_retrieve)
    monkeypatch.setattr(rag_subgraph, "rerank", fake_rerank)
    memories = [{"content": "La seña para reservar un turno es de 5000 pesos.", "kind": "hecho"}]
    out = await rag_subgraph.crag_app.ainvoke(
        rag_subgraph.initial_rag_state("¿cuánto hay que dejar de seña?", "p", memories=memories)
    )
    assert out["abstained"] is False, f"no debió abstenerse; answer={out['answer']!r}"
    assert "5000" in out["answer"]
    assert out["sources"] == []  # memory-only ⇒ sin tarjeta de fuentes


async def test_merge_precedence_memory_over_doc(monkeypatch):
    """Precedencia: doc dice 60, memoria dice 90 ⇒ la respuesta lidera con 90 (memoria)."""

    async def fake_retrieve(query, practice_id=None, top_k=None):
        return [_chunk("La primera consulta dura 60 minutos.")]

    async def fake_rerank(query, chunks):
        return chunks

    monkeypatch.setattr(rag_subgraph, "retrieve", fake_retrieve)
    monkeypatch.setattr(rag_subgraph, "rerank", fake_rerank)
    memories = [{"content": "La primera consulta ahora dura 90 minutos.", "kind": "hecho"}]
    out = await rag_subgraph.crag_app.ainvoke(
        rag_subgraph.initial_rag_state("¿cuánto dura la primera consulta?", "p", memories=memories)
    )
    assert out["abstained"] is False, f"answer={out['answer']!r}"
    assert "90" in out["answer"], f"precedencia de memoria; answer={out['answer']!r}"
