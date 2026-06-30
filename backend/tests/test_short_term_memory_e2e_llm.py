from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state


async def _status(appt_id: str) -> str:
    pool = await db.get_pool()
    return await pool.fetchval("SELECT status FROM appointments WHERE id = $1", appt_id)


async def _seed_client_two_appts(pid: str) -> tuple[str, str, list[str]]:
    """Cliente único raro con DOS turnos futuros → ambigüedad de TURNO determinística."""
    from seed_demo import seed_demo

    await seed_demo()
    prac = (await db.list_active_practitioners(pid))[0]
    pool = await db.get_pool()
    full_name = "Casimiro Testmemo " + uuid4().hex[:6]
    client_id = await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text",
        pid,
        full_name,
    )
    ids = []
    try:
        for d in (3, 6):  # lunes/jueves ficticios: dos días distintos
            start = datetime.now(UTC) + timedelta(days=d)
            a = await db.create_appointment(
                pid, client_id, prac["id"], start, start + timedelta(minutes=30)
            )
            ids.append(a["id"])
    except Exception:
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)
        raise
    return full_name, client_id, ids


@pytest.mark.llm
@pytest.mark.integration
async def test_slotfill_appointment_disambiguation_cancels_chosen() -> None:
    pid = get_settings().practice_id
    full_name, client_id, appt_ids = await _seed_client_two_appts(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "e2e-memo-appt"}}
        # Turno 1: pedido ambiguo (dos turnos) → pregunta cuál
        await graph.ainvoke(
            new_state(f"cancelá el turno de {full_name}", pid, "e2e-memo-appt"), cfg
        )
        snap = await graph.aget_state(cfg)
        assert snap.values["pending_clarification"] is not None
        assert snap.values["pending_clarification"]["stage"] == "appointment"
        # Turno 2: elijo "el primero" → abre la tarjeta
        await graph.ainvoke({"messages": [HumanMessage(content="el primero")]}, cfg)
        snap = await graph.aget_state(cfg)
        assert snap.next == ("confirm_action",)
        await graph.ainvoke(Command(resume="confirm"), cfg)
        cancelled = [a for a in appt_ids if await _status(a) == "cancelado"]
        assert len(cancelled) == 1  # se canceló exactamente UNO (el elegido)
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)


@pytest.mark.llm
@pytest.mark.integration
async def test_chitchat_remembers_within_thread() -> None:
    pid = get_settings().practice_id
    graph = build_graph(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "e2e-memo-chat"}}
    await graph.ainvoke(
        new_state("hola, mi profesional de referencia es la Dra. Gómez", pid, "e2e-memo-chat"),
        cfg,
    )
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="¿quién dije que es mi profesional?")]}, cfg
    )
    last = out["messages"][-1].content
    assert "Gómez" in last or "Gomez" in last


@pytest.mark.llm
@pytest.mark.integration
async def test_no_match_clears_pending() -> None:
    pid = get_settings().practice_id
    full_name, client_id, _ = await _seed_client_two_appts(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "e2e-memo-nomatch"}}
        await graph.ainvoke(
            new_state(f"cancelá el turno de {full_name}", pid, "e2e-memo-nomatch"), cfg
        )
        snap = await graph.aget_state(cfg)
        assert snap.values["pending_clarification"] is not None
        # respuesta que no identifica candidato → limpia el pending
        await graph.ainvoke({"messages": [HumanMessage(content="mejor mostrame otra cosa")]}, cfg)
        snap = await graph.aget_state(cfg)
        assert snap.values.get("pending_clarification") is None  # MemorySaver omits None keys
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)
