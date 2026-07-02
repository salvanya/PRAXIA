import asyncio
import hashlib
import logging

from app import db, embeddings, vectorstore
from app.config import get_settings
from app.guardrails import pii
from app.ingest.chunk import chunk
from app.ingest.parse import parse
from app.models import DocumentSummary

logger = logging.getLogger(__name__)


async def ingest_document(data: bytes, filename: str, doc_type: str, title: str) -> DocumentSummary:
    s = get_settings()
    content_hash = hashlib.sha256(data).hexdigest()
    existing = await db.find_document_by_hash(s.practice_id, content_hash)
    if existing is not None:
        if existing["status"] == "indexado":
            n_chunks = await vectorstore.count_chunks(existing["id"], s.practice_id)
            if n_chunks > 0:
                return DocumentSummary(
                    document_id=existing["id"], status="indexado", n_chunks=n_chunks
                )
            # Drift PG/Qdrant: la fila dice 'indexado' pero no hay vectores. Reusamos
            # la fila y re-indexamos en vez de confiar en un estado inconsistente.
        # Fila previa en 'procesando'/'error'/drift con el mismo contenido: la reusamos
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
        full_text = "\n".join(text for _, text in parsed["pages"])
        pii_summary = await _safe_pii_summary(full_text)
        await db.set_document_status(
            document_id,
            "indexado",
            page_count=page_count,
            pii_summary=pii_summary,
            practice_id=s.practice_id,
        )
        return DocumentSummary(document_id=document_id, status="indexado", n_chunks=len(chunks))
    except Exception:
        await db.set_document_status(document_id, "error", practice_id=s.practice_id)
        raise


async def _safe_pii_summary(text: str) -> dict[str, int] | None:
    """Tag no-destructivo de PII. Fail-open: cualquier fallo → None (no bloquea la ingesta)."""
    try:
        return await asyncio.to_thread(pii.summarize, text)
    except Exception:  # noqa: BLE001 - metadata no-crítica; nunca frena la ingesta
        logger.warning("PII summarize falló; se omite el tag de PII", exc_info=True)
        return None


def _mime(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".txt"):
        return "text/plain"
    if name.endswith((".md", ".markdown")):
        return "text/markdown"
    return "application/octet-stream"
