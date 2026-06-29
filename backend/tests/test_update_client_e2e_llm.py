from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state


async def _seed_client(pid: str) -> tuple[str, str]:
    from seed_demo import seed_demo

    await seed_demo()
    pool = await db.get_pool()
    full_name = "Casimiro Testupd " + uuid4().hex[:6]
    cid = await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name, phone)"
        " VALUES ($1, $2, $3) RETURNING id::text",
        pid,
        full_name,
        "11-0000-0000",
    )
    return full_name, cid


@pytest.mark.llm
@pytest.mark.integration
async def test_update_client_confirm_changes_phone() -> None:
    pid = get_settings().practice_id
    full_name, cid = await _seed_client(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-upd-confirm"}}
        await graph.ainvoke(
            new_state(
                f"cambia el telefono de {full_name} a 11-9999-0000",
                pid,
                "e2e-upd-confirm",
            ),
            config,
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)
        assert snap.tasks[0].interrupts[0].value["kind"] == "update_client"  # clasifico bien
        await graph.ainvoke(Command(resume="confirm"), config)
        phone = (await db.get_client(pid, cid))["phone"]
        assert phone != "11-0000-0000" and phone is not None  # se cambio
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)


@pytest.mark.llm
@pytest.mark.integration
async def test_update_client_decline_leaves_it() -> None:
    pid = get_settings().practice_id
    full_name, cid = await _seed_client(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-upd-decline"}}
        await graph.ainvoke(
            new_state(
                f"cambia el telefono de {full_name} a 11-9999-0000",
                pid,
                "e2e-upd-decline",
            ),
            config,
        )
        await graph.ainvoke(Command(resume="cancel"), config)
        assert (await db.get_client(pid, cid))["phone"] == "11-0000-0000"  # intacto
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
