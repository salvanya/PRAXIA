import asyncio
from datetime import datetime
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
