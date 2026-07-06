# Memoria RICA (update/delete/contradicción) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que Praxia pueda **corregir/olvidar** memorias: un hecho nuevo que contradice a uno viejo lo reemplaza (A, automático en `reflect`), y el usuario puede pedir "olvidá/corregí que…" (B, comando inline con eco).

**Architecture:** Dos caminos que comparten primitivas hard-delete en `app/memory/long_term.py` (`probe`, `forget`, `store` extendido). A = juez e4b 3-vías (`duplicate`/`supersede`/`distinct`) sobre los vecinos ≥0.6 dentro de `reflect`. B = 6ta intención del router (`memoria`) → nodo `memory_command_node` que extrae la operación, hace un find dirigido, borra/supersede y ecoa, con self-verify (misroute → chitchat, nunca borra) y salto de `consolidate` (evita re-aprender lo olvidado).

**Tech Stack:** Python 3.11 async, FastAPI, LangGraph, Ollama (`gemma4:e4b` para juez/extracción), Qdrant (cosine, colección `praxia_memories`), PostgreSQL (`memories`), pytest (`asyncio` auto). Spec: `docs/superpowers/specs/2026-07-06-memoria-rica-design.md`.

## Global Constraints

- **Local-first, $0:** inferencia solo por Ollama local (`make_llm(get_settings().ollama_model_cheap)` = e4b); cero llamadas de red salientes nuevas.
- **Multi-tenant:** toda query/borrado filtra por `practice_id` (Qdrant `_practice_filter` + `scope='practice'`; PG `AND practice_id`).
- **Hard delete, cero DDL:** no se cambia `schema.sql`. Se borran filas PG + puntos Qdrant.
- **Best-effort en A:** vive dentro de `reflect.run` (ya con `asyncio.wait_for(memory_reflect_timeout_s)`); nada de lo que agregues puede romper el turno. Juez None → `"distinct"` (nunca borra por incertidumbre).
- **B nunca borra por error:** self-verify (`operation="none"` → chitchat), sin match → no borra, ambiguo → pide detalle.
- **Commits LIMPIOS:** sin `Co-Authored-By: Claude` ni atribución al asistente (CLAUDE.md §6). Autor = usuario.
- **Loop de dev:** `ruff format .` **antes** de `ruff check .`; `mypy app/` verde (pineado `1.13.*`, no meter ints ≥ 2^64). Imports nuevos en tests EXISTENTES al TOP del archivo (E402).
- **Firma de `store` es cross-cutting:** tras tocarla (Task 3/4) correr la **suite completa** `-m "not llm"`, no solo los archivos tocados.
- **Comandos** (desde `backend/`, docker PG+Qdrant arriba): `.venv\Scripts\python -m pytest tests -m "not llm" -q` · un archivo: `... tests/test_x.py -q` · e2e: `... -m llm -q` (requiere Ollama).

---

### Task 1: `forget` primitive + `recall` con `score` (`long_term.py`)

**Files:**
- Modify: `backend/app/memory/long_term.py` (agregar `forget`; agregar `score` al dict de `recall`)
- Test: `backend/tests/test_long_term_memory.py` (agregar casos; ya tiene `pytestmark = pytest.mark.integration`, fixture `_setup`, helper `_async`, `PRACTICE`, `_V_A`)

**Interfaces:**
- Produces: `async forget(practice_id: str, ids: list[str]) -> int` (borra Qdrant→PG scoped por practice; devuelve filas PG borradas). `recall(...)` ahora incluye `"score": float` en cada dict (además de `id`/`content`/`kind`/`scope`).
- Consumes: existentes `get_client`, `get_pool`, `get_settings`, `_practice_filter`, `models` (de `qdrant_client`).

- [ ] **Step 1: Write the failing tests**

Agregar al final de `backend/tests/test_long_term_memory.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_long_term_memory.py -q`
Expected: FAIL (`AttributeError: module 'app.memory.long_term' has no attribute 'forget'`; y `KeyError: 'score'`).

- [ ] **Step 3: Implement `forget` + `score`**

En `backend/app/memory/long_term.py`, dentro del bucle de `recall`, agregar `score` al dict:

```python
        out.append(
            {
                "id": str(point.id),
                "content": payload["content"],
                "kind": payload.get("kind", "hecho"),
                "scope": payload.get("scope", "practice"),
                "score": point.score,
            }
        )
```

Y agregar la función `forget` (después de `touch_last_used`):

```python
async def forget(practice_id: str, ids: list[str]) -> int:
    """Borra memorias por id (Qdrant PRIMERO, luego PG), ambos lados scoped por practice_id.

    Qdrant primero porque el recall lee el `content` del payload de Qdrant (sin join a PG):
    borrar el vector antes garantiza que un fallo parcial no deje un punto huérfano que el
    recall mostraría como memoria fantasma. Devuelve cuántas filas PG se borraron."""
    if not ids:
        return 0
    s = get_settings()
    await get_client().delete(
        collection_name=s.qdrant_memories_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.HasIdCondition(has_id=list(ids)),
                    models.FieldCondition(
                        key="practice_id", match=models.MatchValue(value=practice_id)
                    ),
                ]
            )
        ),
    )
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM memories WHERE id = ANY($1::uuid[]) AND practice_id = $2",
        list(ids),
        practice_id,
    )
    return int(result.split()[-1]) if result else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_long_term_memory.py -q`
Expected: PASS (nuevos + los 4 existentes intactos).

- [ ] **Step 5: Format, lint, commit**

```bash
.venv/Scripts/python -m ruff format app/memory/long_term.py tests/test_long_term_memory.py
.venv/Scripts/python -m ruff check app/memory/long_term.py tests/test_long_term_memory.py
git add app/memory/long_term.py tests/test_long_term_memory.py
git commit -m "feat(memoria-rica): forget primitive + score en recall"
```

---

### Task 2: `probe` + config de contradicción/comando (`long_term.py`, `config.py`)

**Files:**
- Modify: `backend/app/memory/long_term.py` (dataclasses `Neighbor`/`Probe` + `probe`)
- Modify: `backend/app/config.py` (5 settings nuevos)
- Test: `backend/tests/test_long_term_memory.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces: `@dataclass Neighbor{id: str, content: str, score: float}`; `@dataclass Probe{vector: list[float], related: list[Neighbor]}`; `async probe(practice_id: str, content: str) -> Probe` (vecinos practice-scope con `score >= memory_contradiction_low`, capados a `memory_contradiction_max_candidates`, ordenados por score desc). Config nueva: `memory_contradiction_enabled: bool=True`, `memory_contradiction_low: float=0.6`, `memory_contradiction_max_candidates: int=3`, `memory_command_enabled: bool=True`, `memory_forget_min_score: float=0.6`.
- Consumes: existentes `embed_query`, `get_client`, `_practice_filter`, `get_settings`, `memory_top_k`.

- [ ] **Step 1: Write the failing tests**

Agregar la config al inicio de `backend/tests/test_config.py` (seguir el estilo del archivo; si testea defaults, agregar un caso):

```python
def test_memoria_rica_defaults() -> None:
    from app.config import Settings

    s = Settings()
    assert s.memory_contradiction_enabled is True
    assert s.memory_contradiction_low == 0.6
    assert s.memory_contradiction_max_candidates == 3
    assert s.memory_command_enabled is True
    assert s.memory_forget_min_score == 0.6
```

Agregar helpers de vectores controlados y tests de `probe` en `backend/tests/test_long_term_memory.py`. Poné el `import math` al TOP del archivo (E402) y los helpers junto a `_V_A`:

```python
def _vec(x0: float, x1: float = 0.0) -> list[float]:
    v = [0.0] * 1024
    v[0] = x0
    v[1] = x1
    return v


_ANCHOR = _vec(1.0)
_BAND = _vec(0.75, math.sqrt(1 - 0.75**2))  # coseno 0.75 con _ANCHOR → dentro de banda
_NEAR = _vec(0.95, math.sqrt(1 - 0.95**2))  # coseno 0.95 → casi-idéntico, SIGUE en related (sin techo)
_ORTHO = _vec(0.0, 1.0)  # coseno 0 → fuera
```

```python
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

    b1, b2, b3 = _vec(0.70, math.sqrt(1 - 0.49)), _vec(0.75, math.sqrt(1 - 0.5625)), _vec(
        0.80, math.sqrt(1 - 0.64)
    )
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_config.py tests/test_long_term_memory.py -q`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'memory_contradiction_low'`; `probe` no existe).

- [ ] **Step 3: Implement config + `probe`**

En `backend/app/config.py`, tras `memory_reflect_timeout_s: float = 10.0`:

```python
    # Memoria RICA (Fase 2 Slice 4)
    memory_contradiction_enabled: bool = True  # kill switch de A (auto-supersede)
    memory_contradiction_low: float = 0.6  # piso de similitud para juzgar un vecino (sin techo)
    memory_contradiction_max_candidates: int = 3  # cap de vecinos juzgados por candidato
    memory_command_enabled: bool = True  # kill switch de B (comando olvidá/corregí)
    memory_forget_min_score: float = 0.6  # umbral de confianza del find de B
```

En `backend/app/memory/long_term.py`, agregar `from dataclasses import dataclass` al TOP y las dataclasses + `probe` (tras `_practice_filter`):

```python
@dataclass
class Neighbor:
    id: str
    content: str
    score: float


@dataclass
class Probe:
    vector: list[float]
    related: list[Neighbor]


async def probe(practice_id: str, content: str) -> Probe:
    """Embebe `content` y devuelve los vecinos practice-scope con score >= contradiction_low.

    SIN techo: la distinción duplicado/contradicción es semántica (el coseno no separa
    'mismo hecho reformulado' de 'mismo sujeto, valor cambiado' — 30→45 min puede dar ≥0.9)
    → la decide el juez (reflect.judge_neighbor), no un umbral. Reusa el vector para el store."""
    s = get_settings()
    vector = await embed_query(content)
    result = await get_client().query_points(
        collection_name=s.qdrant_memories_collection,
        query=vector,
        query_filter=_practice_filter(practice_id),
        limit=s.memory_top_k,
        with_payload=True,
    )
    related: list[Neighbor] = []
    for point in result.points:
        if point.score >= s.memory_contradiction_low:
            payload = point.payload or {}
            related.append(
                Neighbor(id=str(point.id), content=payload["content"], score=point.score)
            )
    return Probe(vector=vector, related=related[: s.memory_contradiction_max_candidates])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_config.py tests/test_long_term_memory.py -q`
Expected: PASS.

- [ ] **Step 5: Format, lint, commit**

```bash
.venv/Scripts/python -m ruff format app/config.py app/memory/long_term.py tests/test_config.py tests/test_long_term_memory.py
.venv/Scripts/python -m ruff check app/config.py app/memory/long_term.py tests/test_config.py tests/test_long_term_memory.py
git add app/config.py app/memory/long_term.py tests/test_config.py tests/test_long_term_memory.py
git commit -m "feat(memoria-rica): probe (vecinos >=0.6 sin techo) + config del slice"
```

---

### Task 3: `store` extendido con `vector`/`supersede_ids` (`long_term.py`)

**Files:**
- Modify: `backend/app/memory/long_term.py` (extender `store`)
- Test: `backend/tests/test_long_term_memory.py`

**Interfaces:**
- Produces: `store(practice_id, *, kind, content, source, salience, vector: list[float] | None = None, supersede_ids: list[str] | tuple[str, ...] = ()) -> str | None`. Con `vector=None` → comportamiento legacy (embed + dedup ≥0.9 → `None` si dup). Con `vector` provisto → NO dedup; inserta y luego borra `supersede_ids` (orden seguro: el nuevo queda durable antes de borrar los viejos). Backward-compatible (params nuevos opcionales).
- Consumes: `forget` (Task 1), `probe.vector` (Task 2), existentes `get_pool`/`get_client`/`_top_match`/`touch_last_used`.

- [ ] **Step 1: Write the failing tests**

Agregar a `backend/tests/test_long_term_memory.py`:

```python
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
    a = await long_term.store(PRACTICE, kind="hecho", content="x1", source="reflexion", salience=0.5)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_long_term_memory.py -k "supersede or skips_dedup" -q`
Expected: FAIL (`store() got an unexpected keyword argument 'vector'`).

- [ ] **Step 3: Extend `store`**

Reemplazar la firma y el cuerpo de `store` en `backend/app/memory/long_term.py`:

```python
async def store(
    practice_id: str,
    *,
    kind: str,
    content: str,
    source: str,
    salience: float,
    vector: list[float] | None = None,
    supersede_ids: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Persiste una memoria practice-scope.

    - vector=None: camino legacy → embed + dedup por coseno (≥ memory_dedup_threshold → touch +
      None). Backward-compatible con callers/tests que no probaron el vecindario.
    - vector provisto: el caller ya hizo `probe` → NO se re-deduplica; inserta y LUEGO borra
      `supersede_ids`. Orden seguro: el nuevo queda durable ANTES de borrar los viejos, así un
      fallo intermedio nunca pierde dato (peor caso: viejo+nuevo conviven, autosana)."""
    s = get_settings()
    if vector is None:
        vector = await embed_query(content)
        match = await _top_match(practice_id, vector)
        if match is not None and match[1] >= s.memory_dedup_threshold:
            await touch_last_used([match[0]])
            return None
    mem_id = str(uuid.uuid4())
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO memories (id, practice_id, scope, kind, content, source, salience)
           VALUES ($1, $2, 'practice', $3, $4, $5, $6)""",
        mem_id,
        practice_id,
        kind,
        content,
        source,
        salience,
    )
    try:
        await get_client().upsert(
            collection_name=s.qdrant_memories_collection,
            points=[
                models.PointStruct(
                    id=mem_id,
                    vector=vector,
                    payload={
                        "practice_id": practice_id,
                        "scope": "practice",
                        "kind": kind,
                        "content": content,
                        "salience": salience,
                    },
                )
            ],
        )
    except Exception:
        # compensación: nunca dejar PG-sin-vector (memoria invisible al recall)
        await pool.execute("DELETE FROM memories WHERE id = $1", mem_id)
        raise
    if supersede_ids:
        try:
            await forget(practice_id, list(supersede_ids))
        except Exception:  # noqa: BLE001 - el nuevo ya está durable; un orphan viejo no es fatal
            logger.warning("supersede: forget de viejas falló (orphan, no fatal)", exc_info=True)
    return mem_id
```

- [ ] **Step 4: Run tests to verify they pass (+ regresión del módulo)**

Run: `.venv\Scripts\python -m pytest tests/test_long_term_memory.py -q`
Expected: PASS (incluidos los legacy `test_dedup_skips_near_duplicate`, `test_store_then_recall_roundtrip`).

- [ ] **Step 5: Format, lint, commit**

```bash
.venv/Scripts/python -m ruff format app/memory/long_term.py tests/test_long_term_memory.py
.venv/Scripts/python -m ruff check app/memory/long_term.py tests/test_long_term_memory.py
git add app/memory/long_term.py tests/test_long_term_memory.py
git commit -m "feat(memoria-rica): store con vector precomputado + supersede_ids (orden seguro)"
```

---

### Task 4: juez 3-vías + auto-supersede en `reflect` (camino A)

**Files:**
- Modify: `backend/app/memory/reflect.py` (`NeighborVerdict`, `judge_neighbor`, `_store_candidate`, bucle de `_reflect`)
- Test: `backend/tests/test_reflect.py` (nuevos casos + actualizar `test_gate_true_stores_extracted`)

**Interfaces:**
- Produces: `NeighborVerdict{relation: Literal["duplicate","supersede","distinct"], reason: str}`; `async judge_neighbor(new_content: str, existing_content: str) -> str` (relación; `"distinct"` si e4b None); `async _store_candidate(practice_id, candidate: MemoryCandidate, source: str, salience: float) -> None`.
- Consumes: `long_term.probe`/`store`/`touch_last_used` (Tasks 1-3), `memory_contradiction_enabled` (Task 2), existentes `_structured`, `_cheap_llm`, `MemoryCandidate`, `get_settings`.

- [ ] **Step 1: Write the failing tests**

En `backend/tests/test_reflect.py`, actualizar imports del TOP y el fake `_store` del test existente, y agregar los casos de `_store_candidate`:

```python
from app.memory.long_term import Neighbor, Probe
from app.memory.reflect import NeighborVerdict
```

Reemplazar `test_gate_true_stores_extracted` por (mockea `probe` para que sea unit puro y acepta la firma nueva de `store`):

```python
async def test_gate_true_stores_extracted(monkeypatch) -> None:
    stored: list[dict] = []

    async def _probe(practice_id, content):
        return Probe(vector=[0.0] * 1024, related=[])  # sin vecinos → inserta directo

    async def _store(practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()):
        stored.append({"content": content, "source": source, "salience": salience})
        return "id"

    llms = iter(
        [
            _FakeLLM(GateVerdict(worth_remembering=True, is_explicit=True, reason="explícito")),
            _FakeLLM(
                ExtractedMemories(
                    memories=[MemoryCandidate(kind="hecho", content="Turnos de 30 min.")]
                )
            ),
        ]
    )
    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(reflect, "_cheap_llm", lambda: next(llms))
    await reflect.run("p", "acordate que los turnos duran 30 min", "Dale.")
    assert stored == [{"content": "Turnos de 30 min.", "source": "explicito", "salience": 0.8}]
```

Agregar casos nuevos de `_store_candidate`:

```python
def _cand(content: str) -> MemoryCandidate:
    return MemoryCandidate(kind="hecho", content=content)


async def test_store_candidate_supersede(monkeypatch) -> None:
    seen: dict = {}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[Neighbor("old1", "Turnos de 30 min.", 0.82)])

    async def _store(practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()):
        seen["store"] = {"supersede_ids": list(supersede_ids), "vector": vector, "content": content}
        return "new1"

    async def _touch(ids):
        seen["touch"] = ids

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(reflect.long_term, "touch_last_used", _touch)
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: _FakeLLM(NeighborVerdict(relation="supersede", reason="x"))
    )
    await reflect._store_candidate("p", _cand("Turnos de 45 min."), "reflexion", 0.5)
    assert seen["store"]["supersede_ids"] == ["old1"]
    assert seen["store"]["vector"] == [0.1] * 1024
    assert "touch" not in seen


async def test_store_candidate_duplicate_touches_not_stores(monkeypatch) -> None:
    seen: dict = {"store": False}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[Neighbor("old1", "Turnos de 30 min.", 0.99)])

    async def _store(*a, **k):
        seen["store"] = True

    async def _touch(ids):
        seen["touch"] = ids

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(reflect.long_term, "touch_last_used", _touch)
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: _FakeLLM(NeighborVerdict(relation="duplicate", reason="x"))
    )
    await reflect._store_candidate("p", _cand("Los turnos duran 30 minutos."), "reflexion", 0.5)
    assert seen["touch"] == ["old1"] and seen["store"] is False


async def test_store_candidate_distinct_inserts_without_supersede(monkeypatch) -> None:
    seen: dict = {}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[Neighbor("old1", "Atendemos sábados.", 0.7)])

    async def _store(practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()):
        seen["supersede_ids"] = list(supersede_ids)

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: _FakeLLM(NeighborVerdict(relation="distinct", reason="x"))
    )
    await reflect._store_candidate("p", _cand("Los turnos duran 30 min."), "reflexion", 0.5)
    assert seen["supersede_ids"] == []


async def test_store_candidate_no_related_skips_judge(monkeypatch) -> None:
    seen: dict = {}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[])

    async def _store(practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()):
        seen["supersede_ids"] = list(supersede_ids)

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: (_ for _ in ()).throw(AssertionError("no juzgar sin vecinos"))
    )
    await reflect._store_candidate("p", _cand("Nuevo hecho."), "reflexion", 0.5)
    assert seen["supersede_ids"] == []


async def test_store_candidate_disabled_uses_legacy_store(monkeypatch) -> None:
    from app.config import Settings

    seen: dict = {}

    async def _store(practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()):
        seen["vector"] = vector

    monkeypatch.setattr(
        reflect, "get_settings", lambda: Settings(memory_contradiction_enabled=False)
    )
    monkeypatch.setattr(
        reflect.long_term, "probe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no probe"))
    )
    monkeypatch.setattr(reflect.long_term, "store", _store)
    await reflect._store_candidate("p", _cand("hecho"), "reflexion", 0.5)
    assert seen["vector"] is None  # camino legacy (sin vector → dedup)


async def test_judge_neighbor_none_is_distinct(monkeypatch) -> None:
    monkeypatch.setattr(reflect, "_cheap_llm", lambda: _FakeLLM(None))
    assert await reflect.judge_neighbor("a", "b") == "distinct"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_reflect.py -q`
Expected: FAIL (`cannot import name 'NeighborVerdict'`; `_store_candidate` no existe).

- [ ] **Step 3: Implement judge + `_store_candidate` + rewire `_reflect`**

En `backend/app/memory/reflect.py`, agregar el modelo + prompt (junto a los otros) :

```python
class NeighborVerdict(BaseModel):
    relation: Literal["duplicate", "supersede", "distinct"]
    reason: str


NEIGHBOR_PROMPT = (
    "Dado un HECHO NUEVO y una MEMORIA EXISTENTE de la misma práctica profesional, clasificá su "
    "relación en UNA palabra:\n"
    "- duplicate: dicen lo MISMO (reformulación, sin cambio de valor).\n"
    "- supersede: MISMO sujeto/atributo pero el nuevo CAMBIA, actualiza o niega el valor "
    "(ej: 'los turnos duran 30 min' vs 'los turnos duran 45 min'; 'atendemos sábados' vs 'ya no "
    "atendemos sábados').\n"
    "- distinct: hechos DIFERENTES, complementarios o no relacionados.\n"
    "Ante la duda, distinct (no borres ni deduplifiques)."
)
```

Agregar las funciones (tras `extract`):

```python
async def judge_neighbor(new_content: str, existing_content: str) -> str:
    """Clasifica la relación del hecho nuevo con un vecino existente. 'distinct' si e4b falla
    (fail-safe: nunca borra ni deduplifica por incertidumbre)."""
    out = await _structured(
        NeighborVerdict,
        [
            ("system", NEIGHBOR_PROMPT),
            ("human", f"HECHO NUEVO: {new_content}\nMEMORIA EXISTENTE: {existing_content}"),
        ],
    )
    return out.relation if out is not None else "distinct"


async def _store_candidate(
    practice_id: str, candidate: MemoryCandidate, source: str, salience: float
) -> None:
    """Persiste un candidato resolviendo contradicciones (A). Duplicado → touch; contradicción →
    supersede; distinto → inserta. Con contradiction deshabilitado, store legacy (dedup ≥0.9)."""
    s = get_settings()
    if not s.memory_contradiction_enabled:
        await long_term.store(
            practice_id, kind=candidate.kind, content=candidate.content, source=source, salience=salience
        )
        return
    probe = await long_term.probe(practice_id, candidate.content)
    supersede_ids: list[str] = []
    for neighbor in probe.related:
        relation = await judge_neighbor(candidate.content, neighbor.content)
        if relation == "duplicate":
            await long_term.touch_last_used([neighbor.id])
            return
        if relation == "supersede":
            supersede_ids.append(neighbor.id)
    await long_term.store(
        practice_id,
        kind=candidate.kind,
        content=candidate.content,
        source=source,
        salience=salience,
        vector=probe.vector,
        supersede_ids=supersede_ids,
    )
```

Reemplazar el bucle de `_reflect`:

```python
    for candidate in await extract(user_text, assistant_text):
        await _store_candidate(practice_id, candidate, source, salience)
```

Verificá que `Literal` esté importado (ya lo está: `from typing import Any, Literal`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_reflect.py -q`
Expected: PASS.

- [ ] **Step 5: Suite completa (firma de `store` es cross-cutting) + mypy**

Run: `.venv\Scripts\python -m pytest tests -m "not llm" -q && .venv\Scripts\python -m mypy app/`
Expected: PASS (todo verde; cazá acá cualquier caller de `store`/`reflect` que haya que ajustar).

- [ ] **Step 6: Format, lint, commit**

```bash
.venv/Scripts/python -m ruff format app/memory/reflect.py tests/test_reflect.py
.venv/Scripts/python -m ruff check app/memory/reflect.py tests/test_reflect.py
git add app/memory/reflect.py tests/test_reflect.py
git commit -m "feat(memoria-rica): juez 3-vias + auto-supersede en reflect (camino A)"
```

---

### Task 5: nodo `memory_command_node` (camino B)

**Files:**
- Create: `backend/app/graph/memory_command.py`
- Test: `backend/tests/test_memory_command.py`

**Interfaces:**
- Produces: `MemoryCommand{operation: Literal["forget","correct","none"], target: str, new_value: str}`; `async extract_command(text: str) -> MemoryCommand | None`; `async memory_command_node(state: AgentState) -> dict`.
- Consumes: `long_term.recall`/`forget`/`store` (Tasks 1-3), `memory_command_enabled`/`memory_forget_min_score`/`memory_dedup_threshold` (config), `chitchat_node`/`write_token`/`write_sources` (de `app.graph.nodes`), `last_user_text`/`AgentState`.

- [ ] **Step 1: Write the failing tests**

Crear `backend/tests/test_memory_command.py`:

```python
from langgraph.graph import END, START, StateGraph

from app.graph import memory_command
from app.graph.memory_command import MemoryCommand
from app.graph.state import AgentState, new_state


def _one_node_graph(node):
    g = StateGraph(AgentState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


async def _run(node, state):
    graph = _one_node_graph(node)
    tokens = ""
    async for chunk in graph.astream(state, stream_mode="custom"):
        if chunk["kind"] == "token":
            tokens += chunk["text"]
    return tokens


def _match(mid="m1", content="Los turnos duran 30 minutos.", score=0.95, kind="hecho"):
    return {"id": mid, "content": content, "kind": kind, "scope": "practice", "score": score}


async def test_forget_confident_deletes_and_echoes(monkeypatch):
    calls = {}

    async def _extract(text):
        return MemoryCommand(operation="forget", target="duración de turnos", new_value="")

    async def _recall(query, practice_id):
        return [_match()]

    async def _forget(practice_id, ids):
        calls["forget"] = ids
        return len(ids)

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    tokens = await _run(memory_command.memory_command_node, new_state("olvidá lo de la duración", "p", "t"))
    assert calls["forget"] == ["m1"]
    assert "olvidé" in tokens.lower()


async def test_no_match_does_not_delete(monkeypatch):
    calls = {"forget": False}

    async def _extract(text):
        return MemoryCommand(operation="forget", target="algo inexistente", new_value="")

    async def _recall(query, practice_id):
        return []

    async def _forget(practice_id, ids):
        calls["forget"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    tokens = await _run(memory_command.memory_command_node, new_state("olvidá X", "p", "t"))
    assert "no tengo nada" in tokens.lower() and calls["forget"] is False


async def test_ambiguous_asks_and_does_not_delete(monkeypatch):
    calls = {"forget": False}

    async def _extract(text):
        return MemoryCommand(operation="forget", target="la duración", new_value="")

    async def _recall(query, practice_id):
        return [_match("m1", "Turnos de 30 min.", 0.72), _match("m2", "Turnos de 45 min.", 0.70)]

    async def _forget(practice_id, ids):
        calls["forget"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    tokens = await _run(memory_command.memory_command_node, new_state("olvidá la duración", "p", "t"))
    assert "parecidas" in tokens.lower() and calls["forget"] is False


async def test_none_falls_back_to_chitchat(monkeypatch):
    called = {"chitchat": False, "forget": False}

    async def _extract(text):
        return MemoryCommand(operation="none", target="", new_value="")

    async def _chitchat(state):
        called["chitchat"] = True
        return {"sources": [], "messages": []}

    async def _forget(practice_id, ids):
        called["forget"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command, "chitchat_node", _chitchat)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    await _run(memory_command.memory_command_node, new_state("hola", "p", "t"))
    assert called["chitchat"] is True and called["forget"] is False


async def test_extract_none_falls_back_to_chitchat(monkeypatch):
    called = {"chitchat": False}

    async def _extract(text):
        return None

    async def _chitchat(state):
        called["chitchat"] = True
        return {"sources": [], "messages": []}

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command, "chitchat_node", _chitchat)
    await _run(memory_command.memory_command_node, new_state("cualquier cosa", "p", "t"))
    assert called["chitchat"] is True


async def test_correct_supersedes_with_new_value(monkeypatch):
    calls = {}

    async def _extract(text):
        return MemoryCommand(
            operation="correct", target="duración de turnos", new_value="Los turnos duran 60 minutos."
        )

    async def _recall(query, practice_id):
        return [_match()]

    async def _store(practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()):
        calls["store"] = {"content": content, "supersede_ids": list(supersede_ids)}
        return "new1"

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "store", _store)
    tokens = await _run(memory_command.memory_command_node, new_state("corregí la duración", "p", "t"))
    assert calls["store"] == {"content": "Los turnos duran 60 minutos.", "supersede_ids": ["m1"]}
    assert "corregido" in tokens.lower()


async def test_correct_without_value_asks_and_does_not_store(monkeypatch):
    calls = {"store": False}

    async def _extract(text):
        return MemoryCommand(operation="correct", target="la duración", new_value="")

    async def _recall(query, practice_id):
        return [_match()]

    async def _store(*a, **k):
        calls["store"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "store", _store)
    tokens = await _run(memory_command.memory_command_node, new_state("corregí la duración", "p", "t"))
    assert "dato correcto" in tokens.lower() and calls["store"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_memory_command.py -q`
Expected: FAIL (`No module named 'app.graph.memory_command'`).

- [ ] **Step 3: Create the node**

Crear `backend/app/graph/memory_command.py`:

```python
import logging
from typing import Any, Literal

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from app.config import get_settings
from app.graph.nodes import chitchat_node, write_sources, write_token
from app.graph.state import AgentState, last_user_text
from app.memory import long_term

logger = logging.getLogger(__name__)


class MemoryCommand(BaseModel):
    operation: Literal["forget", "correct", "none"]
    target: str
    new_value: str


EXTRACT_COMMAND_PROMPT = (
    "El usuario quiere gestionar lo que el asistente RECUERDA de la práctica. Extraé la operación:\n"
    "- operation='forget' si pide OLVIDAR/borrar algo ('olvidá que…', 'ya no…', 'borrá de tu "
    "memoria…').\n"
    "- operation='correct' si pide CORREGIR/actualizar un dato ('corregí que…', 'en realidad…', "
    "'lo correcto es…').\n"
    "- operation='none' si NO es un pedido de olvidar ni corregir.\n"
    "target = el dato viejo a olvidar/corregir, en pocas palabras.\n"
    "new_value = SOLO para 'correct': el dato correcto como frase autocontenida; vacío en el resto."
)


def _cheap_llm() -> Any:
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model_cheap, temperature=0.0)


async def extract_command(text: str) -> MemoryCommand | None:
    """Extrae la operación de memoria del mensaje. None si e4b no decide (patrón router/reflect)."""
    bound = _cheap_llm().with_structured_output(MemoryCommand)
    for _ in range(2):
        try:
            out = await bound.ainvoke([("system", EXTRACT_COMMAND_PROMPT), ("human", text)])
        except Exception:  # noqa: BLE001 - cualquier fallo cuenta como intento
            out = None
        if out is not None:
            return out
    return None


async def memory_command_node(state: AgentState) -> dict:
    """Camino B: olvidá/corregí inline con eco. Self-verify (misroute → chitchat, nunca borra);
    borra solo con match confiable; ambigüedad → pide detalle. Va a END (salta consolidate)."""
    s = get_settings()
    text = last_user_text(state)
    cmd = await extract_command(text) if s.memory_command_enabled else None
    if cmd is None or cmd.operation == "none":
        return await chitchat_node(state)  # no era un comando → chat normal, NO borra

    practice_id = state["practice_id"]
    matches = [
        m for m in await long_term.recall(cmd.target, practice_id)
        if m["score"] >= s.memory_forget_min_score
    ]
    top = matches[0] if matches else None
    confident = top is not None and (top["score"] >= s.memory_dedup_threshold or len(matches) == 1)

    if top is None:
        msg = "No tengo nada guardado sobre eso."
    elif not confident:
        msg = "Encontré varias cosas parecidas; decime con más detalle cuál querés que olvide o corrija."
    elif cmd.operation == "forget":
        await long_term.forget(practice_id, [top["id"]])
        msg = f"Listo, me olvidé de: «{top['content']}»."
    else:  # correct
        new_value = cmd.new_value.strip()
        if not new_value:
            msg = "¿Cuál es el dato correcto? Decímelo y lo actualizo."
        else:
            await long_term.store(
                practice_id,
                kind=top["kind"],
                content=new_value,
                source="explicito",
                salience=0.8,
                supersede_ids=[top["id"]],
            )
            msg = f"Corregido. Ahora recuerdo: «{new_value}»."

    write_token(msg)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=msg)]}
```

Nota: `store(..., supersede_ids=[id])` sin `vector` embebe `new_value` y NO deduplica (supersede_ids fuerza el insert + borra el viejo). Correcto para B.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_memory_command.py -q`
Expected: PASS (7 casos).

- [ ] **Step 5: Format, lint, commit**

```bash
.venv/Scripts/python -m ruff format app/graph/memory_command.py tests/test_memory_command.py
.venv/Scripts/python -m ruff check app/graph/memory_command.py tests/test_memory_command.py
git add app/graph/memory_command.py tests/test_memory_command.py
git commit -m "feat(memoria-rica): nodo memory_command inline con eco (camino B)"
```

---

### Task 6: cableado del router/edges/build (intención `memoria` → nodo → END)

**Files:**
- Modify: `backend/app/graph/router.py` (INTENTS + línea del prompt)
- Modify: `backend/app/graph/edges.py` (`_INTENT_TO_NODE`)
- Modify: `backend/app/graph/build.py` (registrar nodo + edge condicional + edge a END)
- Test: `backend/tests/test_router.py`, `backend/tests/test_edges.py`, `backend/tests/test_build_wiring.py`

**Interfaces:**
- Produces: `router.INTENTS == ("rag", "sql", "action", "chitchat", "memoria", "out_of_scope")`; `_INTENT_TO_NODE["memoria"] == "memory_command"`; grafo con nodo `memory_command` cuyo único edge sale a `END` (no pasa por `consolidate`).
- Consumes: `memory_command_node` (Task 5).

- [ ] **Step 1: Write the failing tests**

Actualizar el contrato de INTENTS en `backend/tests/test_router.py`:

```python
def test_intents_tuple_is_the_contract():
    assert router.INTENTS == ("rag", "sql", "action", "chitchat", "memoria", "out_of_scope")
```

Agregar (usa el `FakeRouterLLM` que ya está en el archivo):

```python
async def test_classify_intent_accepts_memoria():
    assert await router.classify_intent("olvidá eso", llm=FakeRouterLLM("memoria")) == "memoria"
```

Agregar a `backend/tests/test_edges.py`:

```python
def test_memoria_intent_routes_to_memory_command() -> None:
    from app.graph.edges import _INTENT_TO_NODE

    assert _INTENT_TO_NODE["memoria"] == "memory_command"
```

Reemplazar el cuerpo de `test_memory_nodes_are_wired` y agregar el edge-check en `backend/tests/test_build_wiring.py`:

```python
def test_memory_nodes_are_wired() -> None:
    graph = build_graph(checkpointer=None)
    nodes = set(graph.get_graph().nodes)
    assert {"recall", "consolidate", "memory_command"} <= nodes


def test_memory_command_goes_to_end_not_consolidate() -> None:
    graph = build_graph(checkpointer=None)
    g = graph.get_graph()
    targets = {e.target for e in g.edges if e.source == "memory_command"}
    assert "__end__" in targets and "consolidate" not in targets
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_router.py tests/test_edges.py tests/test_build_wiring.py -q`
Expected: FAIL (INTENTS no tiene "memoria"; `_INTENT_TO_NODE` no tiene la clave; nodo no está en el grafo).

- [ ] **Step 3: Wire router + edges + build**

En `backend/app/graph/router.py`: cambiar `INTENTS` y agregar la línea al prompt (antes de `out_of_scope`):

```python
INTENTS: tuple[str, ...] = ("rag", "sql", "action", "chitchat", "memoria", "out_of_scope")
```

En `ROUTER_PROMPT`, insertar tras la línea de `chitchat` (antes de `out_of_scope`):

```python
    "- memoria: el usuario pide OLVIDAR o CORREGIR algo que Praxia recuerda de la práctica. "
    'Ej: "olvidá que los turnos duran 30 min", "ya no atendemos sábados", '
    '"corregí que la primera consulta dura 45 minutos".\n'
```

En `backend/app/graph/edges.py`, agregar la clave a `_INTENT_TO_NODE`:

```python
    "memoria": "memory_command",
```

En `backend/app/graph/build.py`: importar el nodo, registrarlo, agregarlo al dict de edges condicionales de `recall`, y cablearlo a `END`:

```python
from app.graph.memory_command import memory_command_node
```

```python
    g.add_node("memory_command", memory_command_node)
```

En el `add_conditional_edges("recall", route, {...})`, agregar la entrada:

```python
            "memory_command": "memory_command",
```

Y junto a `g.add_edge("scope_reject", END)`:

```python
    g.add_edge("memory_command", END)  # salta consolidate: no reflect → no re-aprende lo olvidado
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_router.py tests/test_edges.py tests/test_build_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Suite completa + mypy**

Run: `.venv\Scripts\python -m pytest tests -m "not llm" -q && .venv\Scripts\python -m mypy app/`
Expected: PASS (verificá que ningún test asuma las 5 intenciones viejas ni la topología anterior).

- [ ] **Step 6: Format, lint, commit**

```bash
.venv/Scripts/python -m ruff format app/graph/router.py app/graph/edges.py app/graph/build.py tests/test_router.py tests/test_edges.py tests/test_build_wiring.py
.venv/Scripts/python -m ruff check app/graph/router.py app/graph/edges.py app/graph/build.py tests/test_router.py tests/test_edges.py tests/test_build_wiring.py
git add app/graph/router.py app/graph/edges.py app/graph/build.py tests/test_router.py tests/test_edges.py tests/test_build_wiring.py
git commit -m "feat(memoria-rica): intencion 'memoria' -> memory_command -> END"
```

---

### Task 7: e2e-llm (A supersede real + B forget real)

**Files:**
- Create: `backend/tests/test_memory_rica_e2e_llm.py`

**Interfaces:**
- Consumes: todo lo anterior (grafo real + Ollama e4b + PG + Qdrant). Marker `llm` (fuera del gate `-m "not llm"`).

- [ ] **Step 1: Write the e2e tests**

Crear `backend/tests/test_memory_rica_e2e_llm.py`:

```python
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
        PRACTICE, kind="hecho", content="Los turnos duran 30 minutos.", source="reflexion", salience=0.5
    )
    await reflect._store_candidate(
        PRACTICE, MemoryCandidate(kind="hecho", content="Los turnos duran 45 minutos."), "reflexion", 0.5
    )
    contents = await _contents()
    assert "45" in contents, f"la nueva debió persistir; got: {contents!r}"
    assert "30" not in contents, f"la contradictoria debió superseder; got: {contents!r}"


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
        "SELECT count(*) FROM memories WHERE practice_id = $1 AND content ILIKE '%sábado%'", PRACTICE
    )
    assert n == 0, "la memoria de sábados debió borrarse"
```

- [ ] **Step 2: Run the e2e tests (requiere Ollama + docker)**

Run: `.venv\Scripts\python -m pytest tests/test_memory_rica_e2e_llm.py -q`
Expected: PASS. Si `test_B` falla por routing (e4b no clasificó "memoria"), reintentá; si es sistemático, endurecé la línea del prompt del router (§Task 6) — NO relajes la aserción del borrado.

- [ ] **Step 3: Commit**

```bash
.venv/Scripts/python -m ruff format tests/test_memory_rica_e2e_llm.py
.venv/Scripts/python -m ruff check tests/test_memory_rica_e2e_llm.py
git add tests/test_memory_rica_e2e_llm.py
git commit -m "test(memoria-rica): e2e-llm supersede (A) + forget por comando (B)"
```

---

## Cierre del slice (tras Task 7)

- [ ] **Gate final:** `.venv\Scripts\python -m pytest tests -m "not llm" -q` (verde) + `.venv\Scripts\python -m mypy app/` (Success) + `.venv\Scripts\python -m app.eval.run` (sin regresión: los golden actuales no disparan contradicción/comando).
- [ ] **Smoke navegador** (docker + Ollama + `seed_demo.py` + `dev.py` en `:8000` + front `:3100`):
  1. Decí "acordate que los turnos duran 30 minutos" → luego "en realidad los turnos ahora duran 45 minutos" → preguntá "¿cuánto duran los turnos?" → responde **45** (no ambas).
  2. "olvidá que los turnos duran 45 minutos" → confirma con eco y deja de saberlo.
  3. "agendá un turno para Ana mañana a las 10" → **sigue abriendo la ConfirmCard** (HITL intacto).
- [ ] **Merge** a `main` con `--no-ff`, commit limpio (sin atribución a Claude), y borrar la rama `fase2/memoria-rica`.
- [ ] Actualizar `docs/NEXT_SESSION.md` y la memoria del proyecto (cierre del slice; próximo = DSPy).

## Notas de review (whole-branch)
- **Correctitud de A:** el juez 3-vías se corre sobre TODOS los vecinos ≥0.6 (sin techo 0.9) — verificá que no reintrodujo un corte dedup que enmascare 30→45.
- **Seguridad de B:** confirmá que ningún camino borra sin match confiable (misroute/none/sin-match/ambiguo).
- **Orden PG↔Qdrant:** `store` inserta antes de borrar; `forget` borra Qdrant antes que PG. Sin memoria fantasma.
- **HITL intacto:** `memory_command` NO usa `interrupt`; las escrituras CRM siguen con ConfirmCard.
- **Best-effort:** A no puede romper el turno (vive en `reflect.run` time-boxed).
