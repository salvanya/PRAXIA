from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state


async def _seed_client_with_appt(pid: str) -> tuple[str, str, str, datetime]:
    """Cliente unico con UN turno futuro. Devuelve (full_name, cid, aid, start)."""
    from seed_demo import seed_demo

    await seed_demo()
    prac = (await db.list_active_practitioners(pid))[0]
    pool = await db.get_pool()
    full_name = "Casimiro Testresched " + uuid4().hex[:6]
    cid = await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text",
        pid,
        full_name,
    )
    start = datetime.now(UTC) + timedelta(days=5)
    appt = await db.create_appointment(pid, cid, prac["id"], start, start + timedelta(minutes=30))
    return full_name, cid, appt["id"], start


async def _row(appt_id: str) -> tuple[datetime, str]:
    pool = await db.get_pool()
    r = await pool.fetchrow("SELECT start_at, status FROM appointments WHERE id = $1", appt_id)
    return r["start_at"], r["status"]


@pytest.mark.llm
@pytest.mark.integration
async def test_reschedule_confirm_moves_appointment() -> None:
    pid = get_settings().practice_id
    full_name, cid, aid, original_start = await _seed_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-resched-confirm"}}
        await graph.ainvoke(
            new_state(
                f"reprogramá el turno de {full_name} para mañana a las 15:00",
                pid,
                "e2e-resched-confirm",
            ),
            config,
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)
        assert snap.tasks[0].interrupts[0].value["kind"] == "reschedule_appointment"
        await graph.ainvoke(Command(resume="confirm"), config)
        new_start, status = await _row(aid)
        assert status == "programado"
        assert new_start != original_start
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)


@pytest.mark.llm
@pytest.mark.integration
async def test_reschedule_decline_leaves_it() -> None:
    pid = get_settings().practice_id
    full_name, cid, aid, original_start = await _seed_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-resched-decline"}}
        await graph.ainvoke(
            new_state(
                f"reprogramá el turno de {full_name} para mañana a las 15:00",
                pid,
                "e2e-resched-decline",
            ),
            config,
        )
        assert (await graph.aget_state(config)).next == ("confirm_action",)
        await graph.ainvoke(Command(resume="cancel"), config)
        new_start, status = await _row(aid)
        assert new_start == original_start and status == "programado"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
