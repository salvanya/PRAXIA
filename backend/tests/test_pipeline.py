import os
from pathlib import Path

import pytest

from app import db, vectorstore
from app.ingest import pipeline
from app.ingest.pipeline import _mime, ingest_document

FIXTURES = Path(__file__).parent / "fixtures"


def test_mime_maps_known_suffixes():
    assert _mime("a.pdf") == "application/pdf"
    assert _mime("a.PDF") == "application/pdf"
    assert _mime("a.txt") == "text/plain"
    assert _mime("a.md") == "text/markdown"
    assert _mime("a.markdown") == "text/markdown"
    assert _mime("a.bin") == "application/octet-stream"


@pytest.mark.integration
async def test_ingest_markdown_indexes():
    await vectorstore.ensure_collection()
    data = (FIXTURES / "protocolo.md").read_bytes()
    summary = await ingest_document(
        data, "protocolo.md", "protocolo", "Protocolo de primera consulta"
    )

    assert summary["status"] == "indexado"
    assert summary["n_chunks"] >= 1
    docs = await db.list_documents(os.environ["PRACTICE_ID"])
    assert any(d["id"] == summary["document_id"] and d["status"] == "indexado" for d in docs)


@pytest.mark.integration
async def test_ingest_same_content_dedups():
    await vectorstore.ensure_collection()
    data = (FIXTURES / "protocolo.md").read_bytes()
    first = await ingest_document(data, "protocolo.md", "protocolo", "Dedup A")
    second = await ingest_document(data, "protocolo.md", "protocolo", "Dedup B")

    assert second["document_id"] == first["document_id"]
    assert second["status"] == "indexado"
    docs = await db.list_documents(os.environ["PRACTICE_ID"])
    assert sum(1 for d in docs if d["id"] == first["document_id"]) == 1


@pytest.mark.integration
async def test_reindexes_when_vectors_missing():
    await vectorstore.ensure_collection()
    data = b"# Drift\nContenido unico para el test de drift PG/Qdrant.\n"
    first = await ingest_document(data, "drift.md", "protocolo", "Drift")
    assert first["n_chunks"] >= 1
    # Drift: Postgres dice 'indexado' pero los vectores ya no están en Qdrant.
    await vectorstore.delete_document(first["document_id"], os.environ["PRACTICE_ID"])
    again = await ingest_document(data, "drift.md", "protocolo", "Drift")
    assert again["document_id"] == first["document_id"]  # reusa la fila, no duplica
    assert again["n_chunks"] >= 1  # se re-indexó en vez de devolver 0


@pytest.mark.integration
async def test_ingest_error_marks_document_status_error():
    bad = b"\n\n   \n"  # markdown sin texto extraíble
    with pytest.raises(ValueError):
        await ingest_document(bad, "vacio2.md", "protocolo", "Sin texto")
    import hashlib

    existing = await db.find_document_by_hash(
        os.environ["PRACTICE_ID"], hashlib.sha256(bad).hexdigest()
    )
    assert existing is not None
    assert existing["status"] == "error"


@pytest.mark.integration
async def test_reingest_after_error_does_not_violate_hash_unique():
    await vectorstore.ensure_collection()
    bad = b"   \n   "  # markdown sin texto extraíble -> error
    with pytest.raises(ValueError):
        await ingest_document(bad, "vacio.md", "protocolo", "Vacío")
    # Reintentar el MISMO contenido debe reusar la fila en error (mismo hash),
    # no insertar una nueva (que violaría el índice único content_hash).
    with pytest.raises(ValueError):
        await ingest_document(bad, "vacio.md", "protocolo", "Vacío")


async def test_safe_pii_summary_returns_counts(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.pii, "summarize", lambda text: {"PERSON": 2})
    assert await pipeline._safe_pii_summary("Ana y Beto") == {"PERSON": 2}


async def test_safe_pii_summary_fail_open(monkeypatch) -> None:
    def _boom(text: str) -> dict:
        raise pipeline.pii.PiiUnavailable("no model")

    monkeypatch.setattr(pipeline.pii, "summarize", _boom)
    assert await pipeline._safe_pii_summary("Ana") is None  # fail-open, no relanza


@pytest.mark.integration
async def test_ingest_persists_pii_summary(monkeypatch) -> None:
    await vectorstore.ensure_collection()
    monkeypatch.setattr(pipeline.pii, "summarize", lambda text: {"PERSON": 2, "AR_DNI": 1})
    data = b"# Doc PII\nJuan Perez, DNI 12.345.678.\n"
    summary = await ingest_document(data, "pii_doc.md", "protocolo", "Doc PII")
    doc = await db.get_document(os.environ["PRACTICE_ID"], summary["document_id"])
    assert doc is not None
    assert doc["pii_summary"] == {"PERSON": 2, "AR_DNI": 1}
