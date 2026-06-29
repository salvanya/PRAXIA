import os
import uuid
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import db
from app.config import get_settings


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


async def _new_client(pid: str, full_name: str) -> str:
    pool = await db.get_pool()
    return await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text",
        pid,
        full_name,
    )


@pytest.mark.integration
async def test_find_cancellable_only_future_and_open_statuses() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    prac = (await db.list_active_practitioners(pid))[0]
    cid = await _new_client(pid, "Find Cancelable " + uuid4().hex[:6])
    other = await _new_client(pid, "Otro Cliente " + uuid4().hex[:6])
    now = datetime.now(UTC)
    try:
        future1 = now + timedelta(days=1)
        future2 = now + timedelta(days=2)
        # ofrecibles
        a_prog = await db.create_appointment(
            pid, cid, prac["id"], future2, future2 + timedelta(minutes=30)
        )
        a_conf = await db.create_appointment(
            pid, cid, prac["id"], future1, future1 + timedelta(minutes=30), status="confirmado"
        )
        # excluidos: pasado, atendido, otro cliente
        await db.create_appointment(
            pid,
            cid,
            prac["id"],
            now - timedelta(days=1),
            now - timedelta(days=1) + timedelta(minutes=30),
        )
        await db.create_appointment(
            pid, cid, prac["id"], future1, future1 + timedelta(minutes=30), status="atendido"
        )
        await db.create_appointment(
            pid, other, prac["id"], future1, future1 + timedelta(minutes=30)
        )

        rows = await db.find_cancellable_appointments(pid, cid, now=now, limit=10)
        ids = [r["id"] for r in rows]
        assert ids == [a_conf["id"], a_prog["id"]]  # ordenados por start_at (future1 < future2)
        assert all("practitioner_full_name" in r for r in rows)
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = ANY($1::uuid[])", [cid, other])


@pytest.mark.integration
async def test_cancel_appointment_sets_cancelado_and_guards() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    prac = (await db.list_active_practitioners(pid))[0]
    cid = await _new_client(pid, "Cancel Writer " + uuid4().hex[:6])
    now = datetime.now(UTC)
    try:
        future = now + timedelta(days=1)
        appt = await db.create_appointment(
            pid, cid, prac["id"], future, future + timedelta(minutes=30)
        )

        row = await db.cancel_appointment(pid, appt["id"])
        assert row is not None and row["status"] == "cancelado"

        # idempotencia: 2da cancelación no matchea (ya está cancelado)
        assert await db.cancel_appointment(pid, appt["id"]) is None

        # guard de tenant: otra práctica no puede cancelar
        appt2 = await db.create_appointment(
            pid, cid, prac["id"], future, future + timedelta(minutes=30)
        )
        assert await db.cancel_appointment(str(uuid4()), appt2["id"]) is None
        pool = await db.get_pool()
        assert (
            await pool.fetchval("SELECT status FROM appointments WHERE id = $1", appt2["id"])
            == "programado"
        )
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)


@pytest.mark.integration
async def test_reschedule_moves_times_and_guards() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    prac = (await db.list_active_practitioners(pid))[0]
    cid = await _new_client(pid, "Reschedule Writer " + uuid4().hex[:6])
    now = datetime.now(UTC)
    try:
        start = now + timedelta(days=1)
        appt = await db.create_appointment(pid, cid, prac["id"], start, start + timedelta(minutes=30))
        new_start = now + timedelta(days=2)
        new_end = new_start + timedelta(minutes=30)

        row = await db.reschedule_appointment(pid, appt["id"], new_start, new_end)
        assert row is not None and row["status"] == "programado"
        assert row["start_at"] == new_start and row["end_at"] == new_end

        # guard de tenant: otra práctica no puede reprogramar
        assert await db.reschedule_appointment(str(uuid4()), appt["id"], new_start, new_end) is None

        # guard de estado: un turno cancelado no es reprogramable
        await db.cancel_appointment(pid, appt["id"])
        assert await db.reschedule_appointment(pid, appt["id"], new_start, new_end) is None
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
