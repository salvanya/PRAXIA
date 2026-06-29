import asyncio
from datetime import date, datetime
from typing import Any

import asyncpg

from app.config import get_settings

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await asyncpg.create_pool(get_settings().database_url)
    return _pool


async def insert_document(
    practice_id: str,
    doc_type: str,
    title: str,
    file_uri: str,
    mime_type: str,
    content_hash: str | None = None,
) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO documents
            (practice_id, doc_type, title, file_uri, mime_type, content_hash, status)
        VALUES ($1, $2, $3, $4, $5, $6, 'procesando')
        RETURNING id
        """,
        practice_id,
        doc_type,
        title,
        file_uri,
        mime_type,
        content_hash,
    )
    if row is None:
        raise RuntimeError("insert_document: la inserción no devolvió fila")
    return str(row["id"])


async def find_document_by_hash(practice_id: str, content_hash: str) -> dict[str, Any] | None:
    """Devuelve {id, status} del documento con ese contenido en la práctica, o None."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id::text, status FROM documents WHERE practice_id = $1 AND content_hash = $2",
        practice_id,
        content_hash,
    )
    return dict(row) if row is not None else None


async def set_document_status(
    document_id: str, status: str, page_count: int | None = None, *, practice_id: str
) -> None:
    pool = await get_pool()
    result = await pool.execute(
        "UPDATE documents SET status = $2, page_count = $3 WHERE id = $1 AND practice_id = $4",
        document_id,
        status,
        page_count,
        practice_id,
    )
    if result == "UPDATE 0":
        raise RuntimeError(f"set_document_status: no se actualizó el documento {document_id}")


async def list_documents(practice_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, title, doc_type, status, page_count, ingested_at
        FROM documents WHERE practice_id = $1 ORDER BY ingested_at DESC
        """,
        practice_id,
    )
    return [dict(r) for r in rows]


async def run_select(
    sql: str, *, timeout_ms: int, row_limit: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Ejecuta un SELECT ya validado en una transacción READ ONLY.

    Defensa en profundidad: aunque la validación fallara, la transacción no
    puede escribir. `statement_timeout` corta queries lentas; las filas se
    recortan a `row_limit`.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            records = await conn.fetch(sql)
    rows = [dict(r) for r in records[:row_limit]]
    columns = list(rows[0].keys()) if rows else []
    return rows, columns


async def find_clients_by_name(practice_id: str, name: str, *, limit: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, full_name FROM clients
        WHERE practice_id = $1 AND full_name ILIKE '%' || $2 || '%'
        ORDER BY full_name LIMIT $3
        """,
        practice_id,
        name,
        limit,
    )
    return [dict(r) for r in rows]


async def find_practitioners_by_name(
    practice_id: str, name: str, *, limit: int
) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, full_name FROM practitioners
        WHERE practice_id = $1 AND active AND full_name ILIKE '%' || $2 || '%'
        ORDER BY full_name LIMIT $3
        """,
        practice_id,
        name,
        limit,
    )
    return [dict(r) for r in rows]


async def list_active_practitioners(practice_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id::text, full_name FROM practitioners "
        "WHERE practice_id = $1 AND active ORDER BY full_name",
        practice_id,
    )
    return [dict(r) for r in rows]


async def create_appointment(
    practice_id: str,
    client_id: str,
    practitioner_id: str,
    start_at: datetime,
    end_at: datetime,
    *,
    reason: str | None = None,
    channel: str | None = None,
    status: str = "programado",
    created_by: str | None = None,
) -> dict[str, Any]:
    """Tool de escritura parametrizada. Verifica que client y practitioner sean
    de la práctica (defensa en profundidad sobre el resolver) y recién inserta."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ok_client = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM clients WHERE id = $1 AND practice_id = $2)",
                client_id,
                practice_id,
            )
            ok_prac = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM practitioners WHERE id = $1 AND practice_id = $2)",
                practitioner_id,
                practice_id,
            )
            if not (ok_client and ok_prac):
                raise RuntimeError(
                    "create_appointment: cliente/profesional fuera de la práctica o inexistente"
                )
            row = await conn.fetchrow(
                """
                INSERT INTO appointments
                    (practice_id, client_id, practitioner_id, start_at, end_at,
                     status, reason, channel, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id::text, start_at, end_at, status
                """,
                practice_id,
                client_id,
                practitioner_id,
                start_at,
                end_at,
                status,
                reason,
                channel,
                created_by,
            )
    if row is None:
        raise RuntimeError("create_appointment: la inserción no devolvió fila")
    return dict(row)


async def find_cancellable_appointments(
    practice_id: str, client_id: str, *, now: datetime, limit: int
) -> list[dict[str, Any]]:
    """Turnos del cliente que son cancelables: futuros (start_at >= now) y en estado
    'programado'/'confirmado'. Scoped por practice_id. Incluye el nombre del profesional
    para la tarjeta y los mensajes de desambiguación."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT a.id::text, a.start_at, a.end_at, a.status,
               a.practitioner_id::text, p.full_name AS practitioner_full_name
        FROM appointments a
        JOIN practitioners p ON a.practitioner_id = p.id
        WHERE a.practice_id = $1 AND a.client_id = $2
          AND a.start_at >= $3 AND a.status IN ('programado', 'confirmado')
        ORDER BY a.start_at
        LIMIT $4
        """,
        practice_id,
        client_id,
        now,
        limit,
    )
    return [dict(r) for r in rows]


async def cancel_appointment(practice_id: str, appointment_id: str) -> dict[str, Any] | None:
    """Tool de escritura parametrizada: pasa un turno a 'cancelado'. Guard de tenant
    (practice_id) + de estado (solo programado/confirmado → idempotencia y TOCTOU).
    Devuelve la fila actualizada, o None si no matcheó (otra práctica, inexistente, o ya
    no cancelable)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE appointments SET status = 'cancelado'
        WHERE id = $1 AND practice_id = $2 AND status IN ('programado', 'confirmado')
        RETURNING id::text, status, start_at
        """,
        appointment_id,
        practice_id,
    )
    return dict(row) if row is not None else None


async def reschedule_appointment(
    practice_id: str, appointment_id: str, new_start_at: datetime, new_end_at: datetime
) -> dict[str, Any] | None:
    """Tool de escritura parametrizada: mueve un turno a una nueva franja. Guard de tenant
    (practice_id) + de estado (solo programado/confirmado → idempotencia y TOCTOU). Devuelve la
    fila actualizada, o None si no matcheó (otra práctica, inexistente, o ya no reprogramable)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE appointments SET start_at = $3, end_at = $4
        WHERE id = $1 AND practice_id = $2 AND status IN ('programado', 'confirmado')
        RETURNING id::text, start_at, end_at, status
        """,
        appointment_id,
        practice_id,
        new_start_at,
        new_end_at,
    )
    return dict(row) if row is not None else None


async def log_interaction(
    practice_id: str,
    client_id: str,
    *,
    type: str,
    summary: str | None = None,
    content: str | None = None,
    occurred_at: datetime,
    source: str = "agente",
) -> dict[str, Any]:
    """Tool de escritura parametrizada: registra una interacción con un cliente.
    Verifica que el cliente pertenezca a la práctica (defensa en profundidad sobre
    el resolver) y recién inserta."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO interactions
            (practice_id, client_id, type, summary, content, occurred_at, source)
        SELECT $1, $2, $3, $4, $5, $6, $7
        WHERE EXISTS (SELECT 1 FROM clients WHERE id = $2 AND practice_id = $1)
        RETURNING id::text, occurred_at, type
        """,
        practice_id,
        client_id,
        type,
        summary,
        content,
        occurred_at,
        source,
    )
    if row is None:
        raise RuntimeError("log_interaction: cliente fuera de la práctica o inexistente")
    return dict(row)


async def get_client(practice_id: str, client_id: str) -> dict[str, Any] | None:
    """Lee un cliente scopeado por práctica (para el antes→después de update_client)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id::text, full_name, phone, email, status, dob::text
        FROM clients WHERE id = $1 AND practice_id = $2
        """,
        client_id,
        practice_id,
    )
    return dict(row) if row is not None else None


async def update_client(
    practice_id: str,
    client_id: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    status: str | None = None,
    dob: date | None = None,
) -> dict[str, Any] | None:
    """Tool de escritura parametrizada: actualiza campos ESTRUCTURADOS del cliente. COALESCE
    setea solo lo provisto (un None mantiene el valor actual, no borra). Guard de tenant
    (practice_id). El CHECK del schema valida el enum de status. Devuelve la fila actualizada,
    o None si el cliente es de otra práctica / inexistente. NO toca `notes` (texto libre)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE clients SET
            phone = COALESCE($3, phone),
            email = COALESCE($4, email),
            status = COALESCE($5, status),
            dob = COALESCE($6, dob)
        WHERE id = $1 AND practice_id = $2
        RETURNING id::text, full_name, phone, email, status, dob::text
        """,
        client_id,
        practice_id,
        phone,
        email,
        status,
        dob,
    )
    return dict(row) if row is not None else None
