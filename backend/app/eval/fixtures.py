from app.ingest.pipeline import ingest_document

# Corpus RAG mínimo para el eval gate (y el demo): un protocolo con el dato de los 60 min,
# SIN dirección (para que el caso de abstención "¿cuál es la dirección...?" siga siendo válido).
PROTOCOLO_TEXT = """\
Protocolo de atención — Práctica Demo

Duración de las consultas
La primera consulta dura 60 minutos. Las consultas de seguimiento duran 30 minutos.

Política de cancelaciones
Las cancelaciones deben avisarse con al menos 24 horas de anticipación.

Modalidad de atención
Las sesiones se ofrecen de forma presencial y por telellamada, según la preferencia del paciente.

Preparación de la primera consulta
Se recomienda traer estudios previos y una lista de la medicación actual.
"""


async def ensure_rag_fixture() -> int:
    """Asegura (idempotente) el corpus RAG del gate en Qdrant. Reusa la idempotencia de
    ingest_document (dedup por content_hash + auto-heal del drift PG/Qdrant: si la fila dice
    'indexado' pero no hay vectores, re-indexa). Barato si el corpus está intacto; se re-indexa
    solo si un test wipeó la colección. Devuelve n_chunks."""
    summary = await ingest_document(
        PROTOCOLO_TEXT.encode("utf-8"), "protocolo.txt", "protocolo", "Protocolo de atención"
    )
    return summary["n_chunks"]
