from app import db, embeddings, vectorstore
from app.config import get_settings
from app.ingest.chunk import chunk
from app.ingest.parse import parse
from app.models import DocumentSummary


async def ingest_document(data: bytes, filename: str, doc_type: str, title: str) -> DocumentSummary:
    s = get_settings()
    document_id = await db.insert_document(
        s.practice_id,
        doc_type=doc_type,
        title=title,
        file_uri=f"upload://{filename}",
        mime_type=_mime(filename),
    )
    try:
        parsed = parse(data, filename)
        chunks = chunk(parsed, document_id=document_id, title=title, doc_type=doc_type)
        if not chunks:
            raise ValueError("El documento no produjo chunks")
        vectors = await embeddings.embed_texts([c["text"] for c in chunks])
        await vectorstore.ensure_collection()
        await vectorstore.upsert_chunks(chunks, vectors, s.practice_id)
        page_count = len(parsed["pages"])
        await db.set_document_status(
            document_id, "indexado", page_count=page_count, practice_id=s.practice_id
        )
        return DocumentSummary(document_id=document_id, status="indexado", n_chunks=len(chunks))
    except Exception:
        await db.set_document_status(document_id, "error", practice_id=s.practice_id)
        raise


def _mime(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    return "text/markdown"
