import uuid

import pytest

from app.graph.build import build_graph
from app.graph.state import new_state
from app.memory import long_term

pytestmark = pytest.mark.llm

PRACTICE = "00000000-0000-0000-0000-000000000001"


async def _wipe(practice_id: str) -> None:
    from app.db import get_pool

    pool = await get_pool()
    await pool.execute("DELETE FROM memories WHERE practice_id = $1", practice_id)


async def test_memory_survives_across_threads() -> None:
    """Pain del Slice 8: un hecho dicho en un turno debe estar disponible en OTRO thread
    (largo plazo cross-thread, no el checkpointer de corto plazo)."""
    await long_term.ensure_memories_collection()
    await _wipe(PRACTICE)
    graph = build_graph(checkpointer=None)

    # Turno 1 (thread A): comando explícito → reflexión persiste la memoria.
    await graph.ainvoke(
        new_state(
            "acordate que los turnos de seguimiento duran 30 minutos", PRACTICE, uuid.uuid4().hex
        )
    )

    # La memoria quedó en PG (practice-scope).
    from app.db import get_pool

    pool = await get_pool()
    n = await pool.fetchval("SELECT count(*) FROM memories WHERE practice_id = $1", PRACTICE)
    assert n >= 1, "la reflexión debió persistir al menos una memoria"

    # Turno 2 (thread B NUEVO): el recall la recupera cross-thread.
    state2 = await graph.ainvoke(
        new_state("¿cuánto duran los turnos de seguimiento?", PRACTICE, uuid.uuid4().hex)
    )
    contents = " ".join(m["content"] for m in state2.get("memories", []))
    assert "30" in contents, f"la memoria debió recuperarse en el thread nuevo; got: {contents!r}"


async def test_memory_isolated_by_practice() -> None:
    await long_term.ensure_memories_collection()
    await _wipe(PRACTICE)
    graph = build_graph(checkpointer=None)
    await graph.ainvoke(new_state("acordate que atendemos de 9 a 18", PRACTICE, uuid.uuid4().hex))
    state_b = await graph.ainvoke(
        new_state("¿en qué horario atienden?", "practica-fantasma", uuid.uuid4().hex)
    )
    assert state_b.get("memories", []) == [], "otra práctica no puede ver la memoria"
