import pytest

from app.config import get_settings
from app.memory import long_term

pytestmark = pytest.mark.integration

# Práctica de test dedicada (UUID válido; memories.practice_id tiene FK a practices).
PRACTICE = "00000000-0000-0000-0000-0000000000a1"
# vectores unitarios controlados (evita cargar bge-m3 y hace la similitud determinista)
_V_A = [1.0] + [0.0] * 1023
_V_B = [0.0, 1.0] + [0.0] * 1022


def _async(value):
    async def _coro():
        return value

    return _coro()


async def _reset_collection() -> None:
    client = long_term.get_client()
    s = get_settings()
    if await client.collection_exists(s.qdrant_memories_collection):
        await client.delete_collection(s.qdrant_memories_collection)
    await long_term.ensure_memories_collection()


@pytest.fixture(autouse=True)
async def _setup():
    from app.db import get_pool

    pool = await get_pool()
    await pool.execute(
        "INSERT INTO practices (id, name, type) VALUES ($1, 'Test Memoria', 'clinica') "
        "ON CONFLICT (id) DO NOTHING",
        PRACTICE,
    )
    await _reset_collection()
    await pool.execute("DELETE FROM memories WHERE practice_id = $1", PRACTICE)
    yield


async def test_store_then_recall_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_V_A))
    mid = await long_term.store(
        PRACTICE,
        kind="hecho",
        content="Los turnos duran 30 minutos.",
        source="explicito",
        salience=0.8,
    )
    assert mid is not None
    hits = await long_term.recall("cuánto duran los turnos", PRACTICE)
    assert any("30 minutos" in h["content"] for h in hits)


async def test_recall_isolated_by_practice(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_V_A))
    await long_term.store(
        PRACTICE, kind="hecho", content="dato privado", source="reflexion", salience=0.5
    )
    # recall NO escribe PG → el practice_id del recall puede ser cualquier string (filtro Qdrant).
    hits = await long_term.recall("dato", "practica-fantasma")
    assert hits == [], "otra práctica no puede ver la memoria"


async def test_dedup_skips_near_duplicate(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_V_A))
    first = await long_term.store(
        PRACTICE, kind="hecho", content="Turnos de 30 min.", source="reflexion", salience=0.5
    )
    second = await long_term.store(
        PRACTICE, kind="hecho", content="Turnos de 30 min (dup).", source="reflexion", salience=0.5
    )
    assert first is not None and second is None  # mismo vector → duplicado → skip


async def test_recall_respects_min_score(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_V_A))
    await long_term.store(PRACTICE, kind="hecho", content="algo", source="reflexion", salience=0.5)
    monkeypatch.setattr(
        long_term, "embed_query", lambda text: _async(_V_B)
    )  # query ortogonal (score 0)
    hits = await long_term.recall("nada que ver", PRACTICE)
    assert hits == []


async def test_forget_removes_from_pg_and_qdrant(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_V_A))
    mid = await long_term.store(
        PRACTICE, kind="hecho", content="borrame", source="reflexion", salience=0.5
    )
    assert mid is not None
    n = await long_term.forget(PRACTICE, [mid])
    assert n == 1
    assert await long_term.recall("borrame", PRACTICE) == []  # ya no está en Qdrant
    from app.db import get_pool

    pool = await get_pool()
    assert await pool.fetchval("SELECT count(*) FROM memories WHERE id = $1", mid) == 0


async def test_forget_empty_is_noop() -> None:
    assert await long_term.forget(PRACTICE, []) == 0


async def test_forget_scoped_to_practice(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_V_A))
    mid = await long_term.store(
        PRACTICE, kind="hecho", content="mia", source="reflexion", salience=0.5
    )
    # otra práctica NO puede borrar esta memoria (uuid válido, distinto)
    n = await long_term.forget("00000000-0000-0000-0000-0000000000ff", [mid])
    assert n == 0
    assert any("mia" in h["content"] for h in await long_term.recall("mia", PRACTICE))


async def test_recall_includes_score(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_V_A))
    await long_term.store(
        PRACTICE, kind="hecho", content="con score", source="reflexion", salience=0.5
    )
    hits = await long_term.recall("con score", PRACTICE)
    assert hits and isinstance(hits[0]["score"], float)
