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
