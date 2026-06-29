from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state


async def _seed_unique_client_with_appt(pid: str) -> tuple[str, str, str]:
    """Crea un cliente único con UN solo turno futuro cancelable → resolución no ambigua.
    Devuelve (full_name, client_id, appointment_id)."""
    from seed_demo import seed_demo

    await seed_demo()
    prac = (await db.list_active_practitioners(pid))[0]
    pool = await db.get_pool()
    full_name = "Casimiro Testcancel " + uuid4().hex[:6]
    client_id = await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text",
        pid,
        full_name,
    )
    start = datetime.now(UTC) + timedelta(days=3)
    appt = await db.create_appointment(
        pid, client_id, prac["id"], start, start + timedelta(minutes=30)
    )
    return full_name, client_id, appt["id"]


async def _status(appt_id: str) -> str:
    pool = await db.get_pool()
    return await pool.fetchval("SELECT status FROM appointments WHERE id = $1", appt_id)


@pytest.mark.llm
@pytest.mark.integration
async def test_cancel_confirm_sets_cancelado() -> None:
    pid = get_settings().practice_id
    full_name, client_id, appt_id = await _seed_unique_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-cancel-confirm"}}
        await graph.ainvoke(
            new_state(f"cancelá el turno de {full_name}", pid, "e2e-cancel-confirm"), config
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)  # se abrió la tarjeta
        assert snap.tasks[0].interrupts[0].value["kind"] == "cancel_appointment"  # clasificó bien
        await graph.ainvoke(Command(resume="confirm"), config)
        assert await _status(appt_id) == "cancelado"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)  # cascade → appointment


@pytest.mark.llm
@pytest.mark.integration
async def test_cancel_decline_leaves_it() -> None:
    pid = get_settings().practice_id
    full_name, client_id, appt_id = await _seed_unique_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-cancel-decline"}}
        await graph.ainvoke(
            new_state(f"cancelá el turno de {full_name}", pid, "e2e-cancel-decline"), config
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)
        await graph.ainvoke(Command(resume="cancel"), config)
        assert await _status(appt_id) == "programado"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)
