import uuid

import pytest

from app.graph.build import build_graph
from app.graph.state import new_state
from app.memory import long_term, reflect
from app.memory.reflect import MemoryCandidate

pytestmark = pytest.mark.llm

PRACTICE = "00000000-0000-0000-0000-0000000000b4"


@pytest.fixture(autouse=True)
async def _ensure_practice():
    # memories.practice_id tiene FK a practices → sembrar la práctica de test antes de store()
    from app.db import get_pool

    pool = await get_pool()
    await pool.execute(
        "INSERT INTO practices (id, name, type) VALUES ($1, 'Test Rica', 'clinica') "
        "ON CONFLICT (id) DO NOTHING",
        PRACTICE,
    )
    yield


async def _wipe() -> None:
    from app.db import get_pool

    pool = await get_pool()
    await pool.execute("DELETE FROM memories WHERE practice_id = $1", PRACTICE)


async def _contents() -> str:
    from app.db import get_pool

    pool = await get_pool()
    rows = await pool.fetch("SELECT content FROM memories WHERE practice_id = $1", PRACTICE)
    return " | ".join(r["content"] for r in rows)


async def test_A_supersede_replaces_contradicting_fact() -> None:
    """A: un hecho nuevo que contradice a uno viejo lo reemplaza (probe real + juez e4b real)."""
    await long_term.ensure_memories_collection()
    await _wipe()
    await long_term.store(
        PRACTICE,
        kind="hecho",
        content="Los turnos duran 30 minutos.",
        source="reflexion",
        salience=0.5,
    )
    await reflect._store_candidate(
        PRACTICE,
        MemoryCandidate(kind="hecho", content="Los turnos duran 45 minutos."),
        "reflexion",
        0.5,
    )
    contents = await _contents()
    assert "45" in contents, f"la nueva debió persistir; got: {contents!r}"
    assert "30" not in contents, f"la contradictoria debió superseder; got: {contents!r}"


async def test_B_correct_command_supersedes_via_graph() -> None:
    """B-correct: 'corregí: los turnos ahora duran 45 minutos' debe reemplazar el viejo '30 min'."""
    await long_term.ensure_memories_collection()
    await _wipe()
    await long_term.store(
        PRACTICE,
        kind="hecho",
        content="Los turnos duran 30 minutos.",
        source="explicito",
        salience=0.8,
    )
    graph = build_graph(checkpointer=None)
    await graph.ainvoke(
        new_state("corregí: los turnos ahora duran 45 minutos", PRACTICE, uuid.uuid4().hex)
    )
    contents = await _contents()
    assert "45" in contents and "30" not in contents


async def test_B_forget_command_deletes_via_graph() -> None:
    """B: 'olvidá que…' por el grafo real borra la memoria (router → memory_command → forget)."""
    await long_term.ensure_memories_collection()
    await _wipe()
    await long_term.store(
        PRACTICE,
        kind="hecho",
        content="Atendemos los sábados de 9 a 13.",
        source="explicito",
        salience=0.8,
    )
    graph = build_graph(checkpointer=None)
    await graph.ainvoke(new_state("olvidá que atendemos los sábados", PRACTICE, uuid.uuid4().hex))
    from app.db import get_pool

    pool = await get_pool()
    n = await pool.fetchval(
        "SELECT count(*) FROM memories WHERE practice_id = $1 AND content ILIKE '%sábado%'",
        PRACTICE,
    )
    assert n == 0, "la memoria de sábados debió borrarse"


async def test_conversational_update_stored_and_superseded_via_graph() -> None:
    """El smoke destapó que updates conversacionales se perdían por mal routeo a 'memoria'.
    Con el fix, aunque el router mande estos a 'memoria', caen a consolidate→reflect y el hecho
    se guarda/supersede (camino A). Prueba sobre PG (source of truth), no sobre la verbalización."""
    import uuid

    await long_term.ensure_memories_collection()
    await _wipe()
    graph = build_graph(checkpointer=None)
    await graph.ainvoke(
        new_state(
            "acordate que los turnos de práctica duran 30 minutos", PRACTICE, uuid.uuid4().hex
        )
    )
    await graph.ainvoke(
        new_state(
            "en realidad los turnos de práctica ahora duran 45 minutos", PRACTICE, uuid.uuid4().hex
        )
    )
    contents = await _contents()
    assert "45" in contents, f"el update conversacional debió guardarse; got: {contents!r}"
    assert "30" not in contents, f"la contradicción debió superseder; got: {contents!r}"
