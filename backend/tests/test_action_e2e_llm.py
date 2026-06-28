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


async def _count_interactions(pid: str) -> int:
    pool = await db.get_pool()
    return await pool.fetchval("SELECT count(*) FROM interactions WHERE practice_id = $1", pid)


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
    assert snap.next == ("confirm_action",)  # se abrió la confirmación
    assert snap.tasks[0].interrupts[0].value["kind"] == "create_appointment"  # clasificó bien
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
    assert snap.next == ("confirm_action",)  # se abrió la confirmación
    await graph.ainvoke(Command(resume="cancel"), config)
    assert await _count_appointments(pid) == before


@pytest.mark.llm
@pytest.mark.integration
async def test_log_interaction_confirm_writes_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-int-confirm"}}
    before = await _count_interactions(pid)
    await graph.ainvoke(
        new_state(
            f"registrá que llamé a {client['full_name']} y confirmó el turno del martes",
            pid,
            "e2e-int-confirm",
        ),
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_action",)
    assert snap.tasks[0].interrupts[0].value["kind"] == "log_interaction"  # clasificó bien
    await graph.ainvoke(Command(resume="confirm"), config)
    assert await _count_interactions(pid) == before + 1


@pytest.mark.llm
@pytest.mark.integration
async def test_log_interaction_cancel_writes_nothing() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-int-cancel"}}
    before = await _count_interactions(pid)
    await graph.ainvoke(
        new_state(
            f"registrá que le mandé un email a {client['full_name']}",
            pid,
            "e2e-int-cancel",
        ),
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_action",)
    await graph.ainvoke(Command(resume="cancel"), config)
    assert await _count_interactions(pid) == before
