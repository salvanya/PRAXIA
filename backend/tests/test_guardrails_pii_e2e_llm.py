from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state

pytestmark = [pytest.mark.llm, pytest.mark.pii]


async def _last_interaction_content(pid: str, client_id: str) -> str:
    pool = await db.get_pool()
    row = await pool.fetchrow(
        """
        SELECT content FROM interactions
        WHERE practice_id = $1 AND client_id = $2
        ORDER BY occurred_at DESC LIMIT 1
        """,
        pid,
        client_id,
    )
    return row["content"] if row and row["content"] else ""


async def test_log_interaction_persists_redacted_content() -> None:
    """E2E: un mensaje con DNI crudo se persiste redactado en la tarjeta y en DB."""
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]
    name = client["full_name"].split()[0]

    msg = f"registrá que llamé a {name}, me pasó el DNI 30.111.222"

    # Reintentos ante hiccup de Ollama (fail-closed → no interrupt).
    graph: Any = None
    cfg: dict[str, Any] = {}
    snap: Any = None

    for attempt in range(1, 4):
        thread_id = f"pii-e2e-{attempt}"
        g = build_graph(checkpointer=MemorySaver())
        c: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        await g.ainvoke(new_state(msg, pid, thread_id), c)
        s = await g.aget_state(c)
        if s.tasks and s.tasks[0].interrupts:
            graph, cfg, snap = g, c, s
            break

    assert snap is not None and snap.tasks and snap.tasks[0].interrupts, (
        "El grafo nunca abrió la tarjeta de confirmación tras 3 intentos. "
        "Ollama no disponible o el agente siempre abstiene."
    )

    action = snap.tasks[0].interrupts[0].value
    assert (
        action["kind"] == "log_interaction"
    ), f"Se esperaba kind='log_interaction', se obtuvo: {action['kind']!r}"
    content_in_card = action["params"]["content"]
    assert (
        "30.111.222" not in content_in_card
    ), f"PII cruda presente en la tarjeta de confirmación: {content_in_card!r}"
    assert (
        "<DNI>" in content_in_card
    ), f"Placeholder <DNI> ausente en la tarjeta de confirmación: {content_in_card!r}"

    await graph.ainvoke(Command(resume="confirm"), cfg)

    stored = await _last_interaction_content(pid, client["id"])
    assert (
        stored
    ), f"No se encontró fila de interacción en DB para client_id={client['id']!r} tras confirmar."
    assert "30.111.222" not in stored, f"PII cruda persistida en DB: {stored!r}"
    assert "<DNI>" in stored, f"Placeholder <DNI> ausente en la fila persistida en DB: {stored!r}"
