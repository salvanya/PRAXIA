import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state


async def _count_appointments(pid: str) -> int:
    pool = await db.get_pool()
    return await pool.fetchval("SELECT count(*) FROM appointments WHERE practice_id = $1", pid)


@pytest.mark.llm
@pytest.mark.integration
async def test_create_appointment_confirm_writes_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]
    prac = (await db.list_active_practitioners(pid))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-confirm"}}
    before = await _count_appointments(pid)
    await graph.ainvoke(
        new_state(
            f"agendá un turno para {client['full_name']} con {prac['full_name']} mañana a las 10",
            pid,
            "e2e-confirm",
        ),
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_appointment",)  # se abrió la confirmación
    await graph.ainvoke(Command(resume="confirm"), config)
    assert await _count_appointments(pid) == before + 1


@pytest.mark.llm
@pytest.mark.integration
async def test_create_appointment_cancel_writes_nothing() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]
    prac = (await db.list_active_practitioners(pid))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-cancel"}}
    before = await _count_appointments(pid)
    await graph.ainvoke(
        new_state(
            f"agendá un turno para {client['full_name']} con {prac['full_name']} mañana a las 10",
            pid,
            "e2e-cancel",
        ),
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_appointment",)  # se abrió la confirmación
    await graph.ainvoke(Command(resume="cancel"), config)
    assert await _count_appointments(pid) == before
