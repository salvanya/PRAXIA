import os
import uuid

import pytest

from app import db


@pytest.mark.integration
async def test_insert_and_status_roundtrip():
    practice_id = os.environ["PRACTICE_ID"]
    doc_id = await db.insert_document(
        practice_id,
        doc_type="protocolo",
        title="T-" + uuid.uuid4().hex,
        file_uri="mem://x",
        mime_type="text/markdown",
    )
    try:
        await db.set_document_status(doc_id, "indexado", page_count=1, practice_id=practice_id)
        docs = await db.list_documents(practice_id)
        match = [d for d in docs if d["id"] == doc_id]
        assert match and match[0]["status"] == "indexado"
        assert match[0]["page_count"] == 1
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM documents WHERE id = $1", doc_id)
