import os
from pathlib import Path

import pytest

from app import vectorstore
from app.ingest.pipeline import ingest_document
from app.rag.retrieve import retrieve

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.integration
async def test_retrieve_finds_relevant_chunk():
    await vectorstore.ensure_collection()
    await ingest_document(
        (FIXTURES / "protocolo.md").read_bytes(), "protocolo.md",
        "protocolo", "Protocolo de primera consulta",
    )
    hits = await retrieve("¿cuánto dura la primera consulta?", practice_id=os.environ["PRACTICE_ID"])
    assert hits
    assert any("60 minutos" in h["text"] for h in hits)
