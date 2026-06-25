import hashlib

from app import db, embeddings, vectorstore
from app.config import get_settings
from app.ingest.chunk import chunk
from app.ingest.parse import parse
from app.models import DocumentSummary


async def ingest_document(data: bytes, filename: str, doc_type: str, title: str) -> DocumentSummary:
    s = get_settings()
    content_hash = hashlib.sha256(data).hexdigest()
    existing = await db.find_document_by_hash(s.practice_id, content_hash)
    if existing is not None:
        if existing["status"] == "indexado":
            n_chunks = await vectorstore.count_chunks(existing["id"], s.practice_id)
            return DocumentSummary(
                document_id=existing["id"], status="indexado", n_chunks=n_chunks
            )
        # Fila previa en 'procesando'/'error' con el mismo contenido: la reusamos
        # (re-indexar) en vez de insertar otra y violar el índice único.
        document_id = existing["id"]
    else:
        document_id = await db.insert_document(
            s.practice_id,
            doc_type=doc_type,
            title=title,
            file_uri=f"upload://{filename}",
            mime_type=_mime(filename),
            content_hash=content_hash,
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
