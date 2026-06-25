import os
from pathlib import Path

import pytest

from app import db, vectorstore
from app.ingest.pipeline import ingest_document

FIXTURES = Path(__file__).parent / "fixtures"


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
async def test_reingest_after_error_does_not_violate_hash_unique():
    await vectorstore.ensure_collection()
    bad = b"   \n   "  # markdown sin texto extraíble -> error
    with pytest.raises(ValueError):
        await ingest_document(bad, "vacio.md", "protocolo", "Vacío")
    # Reintentar el MISMO contenido debe reusar la fila en error (mismo hash),
    # no insertar una nueva (que violaría el índice único content_hash).
    with pytest.raises(ValueError):
        await ingest_document(bad, "vacio.md", "protocolo", "Vacío")
