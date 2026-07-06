import math

import pytest

from app.config import get_settings
from app.memory import long_term

pytestmark = pytest.mark.integration

# Práctica de test dedicada (UUID válido; memories.practice_id tiene FK a practices).
PRACTICE = "00000000-0000-0000-0000-0000000000a1"
# vectores unitarios controlados (evita cargar bge-m3 y hace la similitud determinista)
_V_A = [1.0] + [0.0] * 1023
_V_B = [0.0, 1.0] + [0.0] * 1022


def _vec(x0: float, x1: float = 0.0, dim1: int = 1) -> list[float]:
    """Vector unitario controlado: componente primaria en dim 0, secundaria en `dim1` (def. 1)."""
    v = [0.0] * 1024
    v[0] = x0
    v[dim1] = x1
    return v


_ANCHOR = _vec(1.0)
_BAND = _vec(0.75, math.sqrt(1 - 0.75**2))  # coseno 0.75 con _ANCHOR → dentro de banda
_NEAR = _vec(
    0.95, math.sqrt(1 - 0.95**2)
)  # coseno 0.95 → casi-idéntico, SIGUE en related (sin techo)
_ORTHO = _vec(0.0, 1.0)  # coseno 0 → fuera


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


async def test_probe_includes_band_and_near_no_ceiling(monkeypatch) -> None:
    vecs = {"ancla": _ANCHOR, "banda": _BAND, "near": _NEAR, "orto": _ORTHO}
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(vecs[text]))
    await long_term.store(PRACTICE, kind="hecho", content="ancla", source="reflexion", salience=0.5)

    banda = await long_term.probe(PRACTICE, "banda")  # 0.75
    assert [n.content for n in banda.related] == ["ancla"]
    assert banda.vector == _BAND

    near = await long_term.probe(PRACTICE, "near")  # 0.95 → SIN techo, sigue siendo related
    assert [n.content for n in near.related] == ["ancla"]

    orto = await long_term.probe(PRACTICE, "orto")  # 0.0 → excluido
    assert orto.related == []


async def test_probe_caps_candidates(monkeypatch) -> None:
    from app.config import Settings

    # Cada vector lleva su componente secundaria en una dimensión distinta (10, 11, 12)
    # para que cos(bi, bj) ≈ xi*xj ≈ 0.5-0.6 << 0.9 y el dedup de store no dispare.
    # cos(bi, _ANCHOR=[1,0,...]) = bi[0] = 0.70/0.75/0.80 exacto (el resto es 0 en dim 0).
    b1 = _vec(0.70, math.sqrt(1 - 0.49), 10)
    b2 = _vec(0.75, math.sqrt(1 - 0.5625), 11)
    b3 = _vec(0.80, math.sqrt(1 - 0.64), 12)
    vecs = {"n1": b1, "n2": b2, "n3": b3, "q": _ANCHOR}
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(vecs[text]))
    for c in ("n1", "n2", "n3"):
        await long_term.store(PRACTICE, kind="hecho", content=c, source="reflexion", salience=0.5)
    monkeypatch.setattr(
        long_term, "get_settings", lambda: Settings(memory_contradiction_max_candidates=2)
    )
    p = await long_term.probe(PRACTICE, "q")
    assert len(p.related) == 2  # los 3 están ≥0.6, pero capea a 2
    assert [round(n.score, 2) for n in p.related] == [0.8, 0.75]  # top-2 por score


async def test_store_supersede_replaces_old(monkeypatch) -> None:
    vecs = {"vieja": _ANCHOR, "nueva": _BAND}
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(vecs[text]))
    old = await long_term.store(
        PRACTICE, kind="hecho", content="vieja", source="reflexion", salience=0.5
    )
    assert old is not None
    new = await long_term.store(
        PRACTICE,
        kind="hecho",
        content="nueva",
        source="reflexion",
        salience=0.5,
        vector=_BAND,
        supersede_ids=[old],
    )
    assert new is not None and new != old
    from app.db import get_pool

    pool = await get_pool()
    assert await pool.fetchval("SELECT count(*) FROM memories WHERE id = $1", old) == 0  # se fue
    assert await pool.fetchval("SELECT count(*) FROM memories WHERE id = $1", new) == 1
    contents = [h["content"] for h in await long_term.recall("nueva", PRACTICE)]
    assert "nueva" in contents and "vieja" not in contents  # también salió de Qdrant


async def test_store_with_vector_skips_dedup(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_ANCHOR))
    a = await long_term.store(
        PRACTICE, kind="hecho", content="x1", source="reflexion", salience=0.5
    )
    # mismo vector: sin `vector` deduplicaría (None); con `vector` provisto NO deduplica
    b = await long_term.store(
        PRACTICE, kind="hecho", content="x2", source="reflexion", salience=0.5, vector=_ANCHOR
    )
    assert a is not None and b is not None


async def test_store_supersede_safe_order_keeps_old_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(long_term, "embed_query", lambda text: _async(_ANCHOR))
    old = await long_term.store(
        PRACTICE, kind="hecho", content="vieja", source="reflexion", salience=0.5
    )

    async def _boom(*a, **k):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(long_term.get_client(), "upsert", _boom)  # el upsert del NUEVO falla
    with pytest.raises(RuntimeError):
        await long_term.store(
            PRACTICE,
            kind="hecho",
            content="nueva",
            source="reflexion",
            salience=0.5,
            vector=_BAND,
            supersede_ids=[old],
        )
    from app.db import get_pool

    pool = await get_pool()
    assert await pool.fetchval("SELECT count(*) FROM memories WHERE id = $1", old) == 1  # SIGUE
