from app.config import get_settings
from app.ingest.chunk import chunk
from app.ingest.parse import ParsedDoc


def test_chunk_assigns_global_index_and_page():
    get_settings.cache_clear()
    long_text = "oración. " * 400  # forces multiple chunks
    parsed = ParsedDoc(pages=[(1, long_text), (2, "página dos corta.")])
    chunks = chunk(parsed, document_id="doc-1", title="Protocolo", doc_type="protocolo")

    assert len(chunks) >= 2
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert chunks[-1]["page"] == 2
    assert all(c["document_id"] == "doc-1" for c in chunks)


def test_chunk_empty_pages_yields_nothing():
    parsed = ParsedDoc(pages=[(1, "   ")])
    assert chunk(parsed, document_id="d", title="t", doc_type="protocolo") == []
