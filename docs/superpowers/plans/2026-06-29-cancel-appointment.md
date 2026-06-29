# cancel_appointment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar la 3ª write-tool de Praxia — `cancel_appointment` (cancelar un turno existente) — sobre el registry del Slice 5, sin tocar router/transporte/front.

**Architecture:** Es la 1ª tool que **muta** una fila (las dos previas son INSERT). La única pieza nueva es un resolver de turno objetivo fail-closed (`resolve_single_appointment`, simétrico a `resolve_single_client`); el resto es cableado del registry: un `WriteTool` descriptor + extender el clasificador `e4b`. Finder filtra lo *ofrecible* (futuro + `programado`/`confirmado`); writer guarda lo *mutable* (`practice_id` + `status`). HITL (`interrupt`/`resume`), nodos genéricos, transporte SSE y `ConfirmCard` se reusan.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph (`interrupt`/`Command`, checkpointer Postgres), asyncpg, Pydantic, `langchain-ollama` (`ChatOllama` → `gemma4:12b` extracción / `gemma4:e4b` clasificador), pytest + pytest-asyncio.

## Global Constraints

Copiadas verbatim del spec / CLAUDE.md. Aplican a TODA tarea:

- **Inferencia 100% local por Ollama**; cero llamadas de red salientes fuera de Ollama/Postgres/Qdrant locales.
- **Costo $0**; sin dependencias nuevas (ninguna se necesita).
- **Aislamiento multi-tenant por `practice_id` SIEMPRE**: el finder filtra por `practice_id` + `client_id`; el `UPDATE` re-verifica `practice_id` en el `WHERE`.
- **Escrituras solo por tools parametrizadas (`$n`) + confirmación humana (HITL `interrupt`)**; nunca SQL de escritura del LLM. El LLM nunca produce UUIDs ni SQL: solo `client_name` + pista de fecha.
- **Fail-closed**: cualquier excepción o ambigüedad (cliente o turno) → abstención, sin tarjeta, sin escribir. Nunca adivinar qué turno cancelar.
- **Gotcha de structured output (CLAUDE.md §4)**: `with_structured_output` del `e4b` devuelve `None` intermitente → el clasificador YA usa `ainvoke` + text-parse (no se cambia). Los **args tipados del extractor `12b` SÍ funcionan** con `with_structured_output`.
- **Commits LIMPIOS**: prohibido cualquier `Co-Authored-By: Claude`/atribución a Claude/Anthropic. Autor = el usuario.
- **mypy SIEMPRE con `--config-file backend/pyproject.toml`** (sin eso, falso-positivo `asyncpg [import-untyped]`).
- **Windows**: para nombres de día de semana usar un mapa fijo español, NUNCA `strftime("%A")` (locale-dependiente). Todo en UTC, etiquetado `(UTC)`.
- **Tests del backend**: `backend\.venv\Scripts\python -m pytest ...` desde la raíz del repo; los tests `integration` requieren `docker compose up -d`; los `-m llm` requieren además Ollama con `gemma4:12b` + `gemma4:e4b`.

---

## File Structure

- `backend/app/db.py` — **modify**: `+find_cancellable_appointments` (finder con JOIN practitioners), `+cancel_appointment` (writer UPDATE parametrizado).
- `backend/app/agents/resolvers.py` — **modify**: `+AppointmentResolution` (dataclass) `+resolve_single_appointment` `+` helpers de formato.
- `backend/app/agents/cancel_agent.py` — **create**: `ProposedCancellation` + `propose_cancellation` (extrae + resuelve cliente + resuelve turno).
- `backend/app/agents/write_tools.py` — **modify**: `+_write_cancel` `+format_cancel_receipt` `+REGISTRY["cancel_appointment"]`; `WRITE_KINDS` y `CLASSIFY_PROMPT` extendidos.
- `backend/app/graph/nodes.py` — **modify**: copy de capacidades incluye "cancelar turnos"; cleanup del `or {}` muerto en `confirm_action_node`.
- Tests: `backend/tests/test_db.py`, `test_resolvers.py`, `test_cancel_agent.py` (nuevo), `test_write_tools.py`, `test_nodes.py`, `test_hitl_cycle.py`, `test_cancel_e2e_llm.py` (nuevo).

---

### Task 1: Data layer — finder + writer en `db.py`

**Files:**
- Modify: `backend/app/db.py` (agregar dos funciones tras `create_appointment`)
- Test: `backend/tests/test_db.py`

**Interfaces:**
- Consumes: `db.get_pool()`, `db.create_appointment(...)`, `db.list_active_practitioners(pid)` (existentes).
- Produces:
  - `async def find_cancellable_appointments(practice_id: str, client_id: str, *, now: datetime, limit: int) -> list[dict[str, Any]]` → filas `{id:str, start_at:datetime, end_at:datetime, status:str, practitioner_id:str, practitioner_full_name:str}`, futuras y `status ∈ {programado, confirmado}`, ordenadas por `start_at`.
  - `async def cancel_appointment(practice_id: str, appointment_id: str) -> dict[str, Any] | None` → fila `{id:str, status:str, start_at:datetime}` o `None` si no matcheó.

- [ ] **Step 1: Write the failing tests**

Agregar al final de `backend/tests/test_db.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config import get_settings


async def _new_client(pid: str, full_name: str) -> str:
    pool = await db.get_pool()
    return await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text",
        pid,
        full_name,
    )


@pytest.mark.integration
async def test_find_cancellable_only_future_and_open_statuses() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    prac = (await db.list_active_practitioners(pid))[0]
    cid = await _new_client(pid, "Find Cancelable " + uuid4().hex[:6])
    other = await _new_client(pid, "Otro Cliente " + uuid4().hex[:6])
    now = datetime.now(UTC)
    try:
        future1 = now + timedelta(days=1)
        future2 = now + timedelta(days=2)
        # ofrecibles
        a_prog = await db.create_appointment(pid, cid, prac["id"], future2, future2 + timedelta(minutes=30))
        a_conf = await db.create_appointment(
            pid, cid, prac["id"], future1, future1 + timedelta(minutes=30), status="confirmado"
        )
        # excluidos: pasado, atendido, otro cliente
        await db.create_appointment(
            pid, cid, prac["id"], now - timedelta(days=1), now - timedelta(days=1) + timedelta(minutes=30)
        )
        await db.create_appointment(
            pid, cid, prac["id"], future1, future1 + timedelta(minutes=30), status="atendido"
        )
        await db.create_appointment(pid, other, prac["id"], future1, future1 + timedelta(minutes=30))

        rows = await db.find_cancellable_appointments(pid, cid, now=now, limit=10)
        ids = [r["id"] for r in rows]
        assert ids == [a_conf["id"], a_prog["id"]]  # ordenados por start_at (future1 < future2)
        assert all("practitioner_full_name" in r for r in rows)
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = ANY($1::uuid[])", [cid, other])


@pytest.mark.integration
async def test_cancel_appointment_sets_cancelado_and_guards() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    prac = (await db.list_active_practitioners(pid))[0]
    cid = await _new_client(pid, "Cancel Writer " + uuid4().hex[:6])
    now = datetime.now(UTC)
    try:
        future = now + timedelta(days=1)
        appt = await db.create_appointment(pid, cid, prac["id"], future, future + timedelta(minutes=30))

        row = await db.cancel_appointment(pid, appt["id"])
        assert row is not None and row["status"] == "cancelado"

        # idempotencia: 2da cancelación no matchea (ya está cancelado)
        assert await db.cancel_appointment(pid, appt["id"]) is None

        # guard de tenant: otra práctica no puede cancelar
        appt2 = await db.create_appointment(pid, cid, prac["id"], future, future + timedelta(minutes=30))
        assert await db.cancel_appointment(str(uuid4()), appt2["id"]) is None
        pool = await db.get_pool()
        assert await pool.fetchval("SELECT status FROM appointments WHERE id = $1", appt2["id"]) == "programado"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_db.py::test_find_cancellable_only_future_and_open_statuses backend/tests/test_db.py::test_cancel_appointment_sets_cancelado_and_guards -q`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'find_cancellable_appointments'`.

- [ ] **Step 3: Implement the two functions**

Agregar en `backend/app/db.py` después de `create_appointment` (antes de `log_interaction`):

```python
async def find_cancellable_appointments(
    practice_id: str, client_id: str, *, now: datetime, limit: int
) -> list[dict[str, Any]]:
    """Turnos del cliente que son cancelables: futuros (start_at >= now) y en estado
    'programado'/'confirmado'. Scoped por practice_id. Incluye el nombre del profesional
    para la tarjeta y los mensajes de desambiguación."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT a.id::text, a.start_at, a.end_at, a.status,
               a.practitioner_id::text, p.full_name AS practitioner_full_name
        FROM appointments a
        JOIN practitioners p ON a.practitioner_id = p.id
        WHERE a.practice_id = $1 AND a.client_id = $2
          AND a.start_at >= $3 AND a.status IN ('programado', 'confirmado')
        ORDER BY a.start_at
        LIMIT $4
        """,
        practice_id,
        client_id,
        now,
        limit,
    )
    return [dict(r) for r in rows]


async def cancel_appointment(practice_id: str, appointment_id: str) -> dict[str, Any] | None:
    """Tool de escritura parametrizada: pasa un turno a 'cancelado'. Guard de tenant
    (practice_id) + de estado (solo programado/confirmado → idempotencia y TOCTOU).
    Devuelve la fila actualizada, o None si no matcheó (otra práctica, inexistente, o ya
    no cancelable)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE appointments SET status = 'cancelado'
        WHERE id = $1 AND practice_id = $2 AND status IN ('programado', 'confirmado')
        RETURNING id::text, status, start_at
        """,
        appointment_id,
        practice_id,
    )
    return dict(row) if row is not None else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_db.py -q`
Expected: PASS (todos, incluyendo los previos).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff check backend/app/db.py && backend\.venv\Scripts\python -m ruff format backend/app/db.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_db.py
git commit -m "feat(db): finder y writer de cancelacion de turnos"
```

---

### Task 2: Resolver — `resolve_single_appointment` en `resolvers.py`

**Files:**
- Modify: `backend/app/agents/resolvers.py`
- Test: `backend/tests/test_resolvers.py`

**Interfaces:**
- Consumes: `db.find_cancellable_appointments(practice_id, client_id, *, now, limit)` (Task 1).
- Produces:
  - `@dataclass class AppointmentResolution: appointment: dict[str, Any] | None; abstain_message: str; abstain_reason: str`
  - `async def resolve_single_appointment(practice_id: str, client: dict[str, Any], when: datetime | None, *, now: datetime, limit: int) -> AppointmentResolution` — `abstain_reason ∈ {"appointment_none","appointment_not_found","appointment_ambiguous","ok"}`. `client` trae `{"id","full_name"}`.

- [ ] **Step 1: Write the failing tests**

Agregar al final de `backend/tests/test_resolvers.py`:

```python
from datetime import UTC, datetime

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)
CLIENT = {"id": "c1", "full_name": "Ana López"}


def _appt(aid, dt, prof="Dra. Gómez", status="programado"):  # type: ignore[no-untyped-def]
    return {
        "id": aid,
        "start_at": dt,
        "end_at": dt,
        "status": status,
        "practitioner_id": "p1",
        "practitioner_full_name": prof,
    }


def _patch_appts(monkeypatch, appts):  # type: ignore[no-untyped-def]
    async def _find(practice_id, client_id, *, now, limit):  # type: ignore[no-untyped-def]
        return appts

    monkeypatch.setattr(db, "find_cancellable_appointments", _find)


async def test_appt_none_abstains(monkeypatch) -> None:
    _patch_appts(monkeypatch, [])
    r = await resolvers.resolve_single_appointment("pid", CLIENT, None, now=NOW, limit=5)
    assert r.appointment is None and r.abstain_reason == "appointment_none"


async def test_appt_single_ok(monkeypatch) -> None:
    a = _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
    _patch_appts(monkeypatch, [a])
    r = await resolvers.resolve_single_appointment("pid", CLIENT, None, now=NOW, limit=5)
    assert r.appointment == a and r.abstain_reason == "ok"


async def test_appt_many_no_hint_ambiguous(monkeypatch) -> None:
    _patch_appts(
        monkeypatch,
        [
            _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC)),
            _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC)),
        ],
    )
    r = await resolvers.resolve_single_appointment("pid", CLIENT, None, now=NOW, limit=5)
    assert r.appointment is None and r.abstain_reason == "appointment_ambiguous"
    assert "Ana López" in r.abstain_message


async def test_appt_hint_filters_to_one(monkeypatch) -> None:
    _patch_appts(
        monkeypatch,
        [
            _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC)),
            _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC)),
        ],
    )
    when = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)  # solo el día
    r = await resolvers.resolve_single_appointment("pid", CLIENT, when, now=NOW, limit=5)
    assert r.appointment is not None and r.appointment["id"] == "a2"


async def test_appt_hint_day_with_no_turno_not_found(monkeypatch) -> None:
    _patch_appts(monkeypatch, [_appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC))])
    when = datetime(2026, 7, 9, 0, 0, tzinfo=UTC)
    r = await resolvers.resolve_single_appointment("pid", CLIENT, when, now=NOW, limit=5)
    assert r.appointment is None and r.abstain_reason == "appointment_not_found"
    assert "01/07" in r.abstain_message  # lista los próximos reales


async def test_appt_hint_time_disambiguates_same_day(monkeypatch) -> None:
    _patch_appts(
        monkeypatch,
        [
            _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC)),
            _appt("a2", datetime(2026, 7, 1, 15, 0, tzinfo=UTC)),
        ],
    )
    when = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
    r = await resolvers.resolve_single_appointment("pid", CLIENT, when, now=NOW, limit=5)
    assert r.appointment is not None and r.appointment["id"] == "a2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_resolvers.py -k appt -q`
Expected: FAIL — `AttributeError: module 'app.agents.resolvers' has no attribute 'resolve_single_appointment'`.

- [ ] **Step 3: Implement the resolver**

Agregar en `backend/app/agents/resolvers.py`. Cambiar el import del tope de `from typing import Any` a incluir lo necesario y agregar `datetime`/`time`:

```python
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from app import db
```

Y agregar al final del archivo:

```python
@dataclass
class AppointmentResolution:
    appointment: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str


_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _format_candidate(appt: dict[str, Any]) -> str:
    # Día en español por mapa fijo: strftime("%A") es locale-dependiente (rompe en Windows).
    start = appt["start_at"]
    day = _WEEKDAYS_ES[start.weekday()]
    return f"{day} {start.strftime('%d/%m %H:%M')} con {appt['practitioner_full_name']}"


def _format_list(appts: list[dict[str, Any]]) -> str:
    return "; ".join(_format_candidate(a) for a in appts)


async def resolve_single_appointment(
    practice_id: str,
    client: dict[str, Any],
    when: datetime | None,
    *,
    now: datetime,
    limit: int,
) -> AppointmentResolution:
    """Resuelve a UN turno cancelable del cliente. Fail-closed: 0 / ambiguo → sin turno
    + mensaje cordial. `cands` es la lista completa (para listar); `matches` es el
    subconjunto tras aplicar la pista de fecha opcional."""
    name = client["full_name"]
    cands = await db.find_cancellable_appointments(practice_id, client["id"], now=now, limit=limit)
    if not cands:
        return AppointmentResolution(
            None, f"{name} no tiene turnos próximos para cancelar.", "appointment_none"
        )
    matches = cands
    if when is not None:
        same_day = [a for a in cands if a["start_at"].date() == when.date()]
        if len(same_day) > 1 and when.time() != time(0, 0):
            timed = [
                a
                for a in same_day
                if (a["start_at"].hour, a["start_at"].minute) == (when.hour, when.minute)
            ]
            same_day = timed or same_day  # si la hora no matchea ninguno, se cae al día
        matches = same_day
    if not matches:
        return AppointmentResolution(
            None,
            f"No encontré un turno de {name} para esa fecha. "
            f"Sus próximos turnos: {_format_list(cands)}.",
            "appointment_not_found",
        )
    if len(matches) > 1:
        return AppointmentResolution(
            None,
            f"{name} tiene varios turnos próximos: {_format_list(matches)}. "
            "¿Cuál? Decime la fecha y la hora.",
            "appointment_ambiguous",
        )
    return AppointmentResolution(matches[0], "", "ok")
```

> Nota: `resolvers.py` hoy importa `from dataclasses import dataclass` y `from typing import Any` y `from app import db`. Asegurate de **no duplicar** imports; solo agregá `from datetime import datetime, time` si no está.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_resolvers.py -q`
Expected: PASS (incluye los tests de `resolve_single_client` previos).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff check backend/app/agents/resolvers.py && backend\.venv\Scripts\python -m ruff format backend/app/agents/resolvers.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/resolvers.py backend/tests/test_resolvers.py
git commit -m "feat(resolvers): resolve_single_appointment fail-closed"
```

---

### Task 3: Agente — `cancel_agent.py` (`propose_cancellation`)

**Files:**
- Create: `backend/app/agents/cancel_agent.py`
- Test: `backend/tests/test_cancel_agent.py` (nuevo)

**Interfaces:**
- Consumes: `ProposalResult` (de `action_agent`), `resolve_single_client` + `resolve_single_appointment` (de `resolvers`), `make_llm`, `get_settings`.
- Produces:
  - `class ProposedCancellation(BaseModel): client_name: str; when: str | None = None`
  - `async def propose_cancellation(question: str, practice_id: str, *, now: datetime, gen_llm: Any = None) -> ProposalResult` — `proposed_action = {"kind":"cancel_appointment", "summary":<card>, "params":{"appointment_id","client_name","practitioner_name","start_at"}}`.

- [ ] **Step 1: Write the failing tests**

Crear `backend/tests/test_cancel_agent.py`:

```python
from datetime import UTC, datetime

from app import db
from app.agents import cancel_agent
from app.agents.cancel_agent import ProposedCancellation

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedCancellation) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedCancellation) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _appt(aid="a1", dt=datetime(2026, 7, 1, 10, 0, tzinfo=UTC)):  # type: ignore[no-untyped-def]
    return {
        "id": aid,
        "start_at": dt,
        "end_at": dt,
        "status": "programado",
        "practitioner_id": "p1",
        "practitioner_full_name": "Dra. Gómez",
    }


def _patch(monkeypatch, clients, appts):  # type: ignore[no-untyped-def]
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    async def _find_appts(practice_id, client_id, *, now, limit):  # type: ignore[no-untyped-def]
        return appts

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "find_cancellable_appointments", _find_appts)


async def test_happy_builds_action(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None and pa["kind"] == "cancel_appointment"
    assert pa["params"]["appointment_id"] == "a1"
    assert "Ana López" in pa["summary"] and "Dra. Gómez" in pa["summary"]


async def test_abstains_extract_fail() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await cancel_agent.propose_cancellation("cancelá", "pid", now=NOW, gen_llm=_LLM())
    assert result.abstained and result.reason == "extract_failed"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [], [])
    llm = FakeGenLLM(ProposedCancellation(client_name="Zzz"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found"


async def test_abstains_appointment_none(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "appointment_none"


async def test_unparseable_when_degrades_to_no_hint(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana", when="no-es-fecha"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained  # when ilegible → None → resolver usa el único candidato
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["appointment_id"] == "a1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_cancel_agent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.cancel_agent'`.

- [ ] **Step 3: Implement the agent**

Crear `backend/app/agents/cancel_agent.py`:

```python
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.agents.action_agent import ProposalResult
from app.agents.resolvers import resolve_single_appointment, resolve_single_client
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = "No pude identificar qué turno cancelar. ¿Me decís el cliente y, si podés, la fecha?"


class ProposedCancellation(BaseModel):
    client_name: str
    when: str | None = None


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt(now: datetime) -> str:
    return (
        "Sos el asistente de agenda de una práctica profesional. A partir del pedido del usuario, "
        "extraé el cliente cuyo turno se va a CANCELAR. La fecha y hora actuales son "
        f"{now.isoformat()} (UTC). Si se menciona la fecha/hora del turno, devolvé 'when' como "
        "fecha/hora ABSOLUTA en ISO 8601 (resolvé 'mañana' o 'el martes' contra la fecha actual). "
        "Si NO se menciona la fecha, dejá 'when' en null. client_name es la persona del turno."
    )


async def _extract(question: str, now: datetime, gen_llm: Any) -> ProposedCancellation | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedCancellation)
    try:
        result = await structured.ainvoke([("system", _system_prompt(now)), ("human", question)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedCancellation) else None


def _card_summary(client_name: str, practitioner_name: str, start: datetime) -> str:
    return (
        f"Cancelar el turno de {client_name} con {practitioner_name} "
        f"el {start.strftime('%d/%m %H:%M')} (UTC)"
    )


async def propose_cancellation(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return ProposalResult(
            proposed_action=None, abstained=True, message=GENERIC_MESSAGE, reason="extract_failed"
        )

    resolution = await resolve_single_client(
        practice_id, extracted.client_name, limit=settings.appt_name_match_limit
    )
    if resolution.client is None:
        return ProposalResult(
            proposed_action=None,
            abstained=True,
            message=resolution.abstain_message,
            reason=resolution.abstain_reason,
        )
    client = resolution.client

    when: datetime | None = None
    if extracted.when:
        try:
            when = datetime.fromisoformat(extracted.when)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        except ValueError:
            when = None  # pista ilegible → se degrada a sin-pista (la fecha es opcional)

    appt_res = await resolve_single_appointment(
        practice_id, client, when, now=now, limit=settings.appt_name_match_limit
    )
    if appt_res.appointment is None:
        return ProposalResult(
            proposed_action=None,
            abstained=True,
            message=appt_res.abstain_message,
            reason=appt_res.abstain_reason,
        )
    appt = appt_res.appointment
    start = appt["start_at"]

    params: dict[str, Any] = {
        "appointment_id": appt["id"],
        "client_name": client["full_name"],
        "practitioner_name": appt["practitioner_full_name"],
        "start_at": start.isoformat(),
    }
    proposed_action = {
        "kind": "cancel_appointment",
        "summary": _card_summary(client["full_name"], appt["practitioner_full_name"], start),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_cancel_agent.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff check backend/app/agents/cancel_agent.py && backend\.venv\Scripts\python -m ruff format backend/app/agents/cancel_agent.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/cancel_agent.py backend/tests/test_cancel_agent.py
git commit -m "feat(agents): propose_cancellation (cancel_agent)"
```

---

### Task 4: Registry + clasificador — `write_tools.py`

**Files:**
- Modify: `backend/app/agents/write_tools.py`
- Test: `backend/tests/test_write_tools.py` (agregar casos + actualizar dos existentes)

**Interfaces:**
- Consumes: `propose_cancellation` (Task 3), `db.cancel_appointment` (Task 1), `WriteTool`/`REGISTRY`/`WRITE_KINDS`/`classify_write_action` (existentes).
- Produces:
  - `async def _write_cancel(practice_id: str, params: dict[str, Any]) -> dict[str, Any]` → `{"cancelled": True, **row}` o `{"cancelled": False}`.
  - `def format_cancel_receipt(params: dict[str, Any], row: dict[str, Any]) -> str`.
  - `REGISTRY["cancel_appointment"]`; `WRITE_KINDS` incluye `"cancel_appointment"`.

- [ ] **Step 1: Update existing tests + write new failing tests**

En `backend/tests/test_write_tools.py`:

(a) **Reemplazar** `test_registry_has_both_tools` por:

```python
def test_registry_has_all_tools() -> None:
    assert set(REGISTRY) == {"create_appointment", "log_interaction", "cancel_appointment"}
    for kind, tool in REGISTRY.items():
        assert tool.kind == kind
        assert tool.cancel_message
```

(b) En `test_classify_returns_kind`, **cambiar** el caso unsupported para que use una frase que SIGUE siendo unsupported (cancelar ahora es una tool):

```python
    assert (
        await classify_write_action("reprogramá el turno", llm=FakeSeqLLM("unsupported"))
        == "unsupported"
    )
```

(c) **Agregar** al final del archivo:

```python
async def test_classify_routes_cancel() -> None:
    assert (
        await classify_write_action(
            "cancelá el turno de Ana", llm=FakeSeqLLM("cancel_appointment")
        )
        == "cancel_appointment"
    )


async def test_write_cancel_adapter_wraps_row(monkeypatch) -> None:
    async def _fake_cancel(practice_id, appointment_id):  # type: ignore[no-untyped-def]
        return {"id": appointment_id, "status": "cancelado", "start_at": None}

    monkeypatch.setattr(write_tools.db, "cancel_appointment", _fake_cancel)
    row = await write_tools._write_cancel("pid", {"appointment_id": "a1", "client_name": "Ana"})
    assert row["cancelled"] is True and row["status"] == "cancelado"


async def test_write_cancel_adapter_handles_none(monkeypatch) -> None:
    async def _fake_cancel(practice_id, appointment_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(write_tools.db, "cancel_appointment", _fake_cancel)
    row = await write_tools._write_cancel("pid", {"appointment_id": "a1"})
    assert row == {"cancelled": False}


def test_cancel_receipt_ok_and_not_ok() -> None:
    params = {
        "client_name": "Ana López",
        "practitioner_name": "Dra. Gómez",
        "start_at": "2026-07-01T10:00:00+00:00",
    }
    ok = write_tools.format_cancel_receipt(params, {"cancelled": True})
    assert "✅" in ok and "Ana López" in ok and "Dra. Gómez" in ok
    bad = write_tools.format_cancel_receipt(params, {"cancelled": False})
    assert "⚠️" in bad
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py -q`
Expected: FAIL — `test_registry_has_all_tools` falla (`cancel_appointment` no está en REGISTRY) y `_write_cancel`/`format_cancel_receipt` no existen (`AttributeError`).

- [ ] **Step 3: Implement registry + classifier changes**

En `backend/app/agents/write_tools.py`:

(a) Agregar el import del nuevo agente (junto a los otros imports de agentes):

```python
from app.agents.cancel_agent import propose_cancellation
```

(b) Agregar las funciones de la tool (después del bloque de `log_interaction`, antes de `REGISTRY`):

```python
# ---- cancel_appointment ----
async def _write_cancel(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = await db.cancel_appointment(practice_id, params["appointment_id"])
    return {"cancelled": True, **row} if row is not None else {"cancelled": False}


def format_cancel_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("cancelled"):
        return (
            "⚠️ No pude cancelar el turno: ya no estaba disponible "
            "(puede haberse cancelado o atendido)."
        )
    start = datetime.fromisoformat(params["start_at"])
    return (
        f"✅ Turno cancelado: {params['client_name']} con {params['practitioner_name']} "
        f"el {start.strftime('%d/%m %H:%M')} (UTC)."
    )
```

(c) Agregar la entrada al `REGISTRY` (dentro del dict literal):

```python
    "cancel_appointment": WriteTool(
        kind="cancel_appointment",
        propose=propose_cancellation,
        write=_write_cancel,
        format_receipt=format_cancel_receipt,
        cancel_message="Listo, dejé el turno como estaba.",
    ),
```

(d) Extender `WRITE_KINDS`:

```python
WRITE_KINDS: tuple[str, ...] = (
    "create_appointment",
    "log_interaction",
    "cancel_appointment",
    "unsupported",
)
```

(e) Reemplazar `CLASSIFY_PROMPT` por la versión extendida (saca cancelar de unsupported; contrasta create=nuevo vs cancel=existente):

```python
CLASSIFY_PROMPT = (
    "Sos el despachador de acciones de escritura de un CRM de prácticas profesionales. "
    "El usuario pidió ejecutar UNA acción que modifica datos. Clasificá QUÉ acción es:\n"
    "- create_appointment: agendar/crear un turno NUEVO. "
    'Ej: "agendá un turno para Ana mañana 10", "dale una cita a Juan el martes", '
    '"reservá un turno con la Dra. Gómez".\n'
    "- log_interaction: registrar/anotar una interacción YA OCURRIDA con un cliente "
    "(sesión, llamada, email, nota, mensaje). "
    'Ej: "registrá que llamé a Ana", "anotá una nota sobre Juan".\n'
    "- cancel_appointment: cancelar/anular un turno YA EXISTENTE. "
    'Ej: "cancelá el turno de Juan", "anulá la cita de Ana del martes", '
    '"cancelá el turno de las 10 de Pedro".\n'
    "- unsupported: cualquier OTRA acción de escritura que NO sea esas tres "
    "(REPROGRAMAR/EDITAR un turno, dar de baja un cliente, facturar). "
    'Ej: "reprogramá el turno de Juan", "cambiá la hora de la cita".\n'
    "Respondé solo con la opción."
)
```

> El mecanismo de `classify_write_action` (`ainvoke` + text-parse + retry + fallback `unsupported`) NO se toca: el match exacto/substring sigue funcionando porque los 4 kinds no se solapan como substrings.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py -q`
Expected: PASS (todos).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff check backend/app/agents/write_tools.py && backend\.venv\Scripts\python -m ruff format backend/app/agents/write_tools.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/write_tools.py backend/tests/test_write_tools.py
git commit -m "feat(write-tools): registrar cancel_appointment + clasificador"
```

---

### Task 5: Cablear en el grafo — copy de capacidades + cleanup + cobertura HITL

**Files:**
- Modify: `backend/app/graph/nodes.py`
- Test: `backend/tests/test_nodes.py`, `backend/tests/test_hitl_cycle.py`

**Interfaces:**
- Consumes: nodos genéricos `propose_action_node`/`confirm_action_node`, `edges.route_after_propose`, `WriteTool` (todos existentes). El dispatch ya es por `kind` → no cambia su lógica.
- Produces: mensaje de capacidades que incluye "cancelar turnos"; `confirm_action_node` sin el `or {}` muerto.

- [ ] **Step 1: Update tests (failing)**

(a) En `backend/tests/test_nodes.py`, en `test_propose_action_unsupported_emits_capabilities`, **agregar** una aserción tras las existentes:

```python
    assert "agendar turnos" in tokens and "registrar interacciones" in tokens
    assert "cancelar turnos" in tokens
    assert sources == []
```

(b) En `backend/tests/test_hitl_cycle.py`, **agregar** la constante `CANCELLATION` junto a `APPOINTMENT`/`INTERACTION`:

```python
CANCELLATION = {
    "kind": "cancel_appointment",
    "summary": "Cancelar el turno de Ana López con Dra. Gómez el 01/07 10:00 (UTC)",
    "params": {"appointment_id": "a1"},
}
```

y **agregar** `("cancel_appointment", CANCELLATION)` a las listas `@pytest.mark.parametrize` de **ambos** tests (`test_confirm_writes_exactly_once` y `test_cancel_writes_nothing`):

```python
@pytest.mark.parametrize(
    "kind,action",
    [
        ("create_appointment", APPOINTMENT),
        ("log_interaction", INTERACTION),
        ("cancel_appointment", CANCELLATION),
    ],
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_nodes.py::test_propose_action_unsupported_emits_capabilities -q`
Expected: FAIL — `assert "cancelar turnos" in tokens` falla (el copy actual no lo incluye). (Los nuevos params de `test_hitl_cycle` pasan porque el dispatch ya es genérico y usa un `WriteTool` fake; esta corrida confirma el copy.)

- [ ] **Step 3: Implement the node changes**

En `backend/app/graph/nodes.py`:

(a) En `propose_action_node`, reemplazar el mensaje de capacidades:

```python
        msg = (
            "Por ahora puedo agendar turnos, registrar interacciones o cancelar turnos. "
            "¿Qué necesitás?"
        )
```

(b) En `confirm_action_node`, reemplazar la 1ª línea (cleanup del `or {}` muerto, fast-follow aprobado):

```python
    action = state["proposed_action"]
    assert action is not None  # route_after_propose garantiza no-None acá
    tool = REGISTRY[action["kind"]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_nodes.py backend/tests/test_hitl_cycle.py -q`
Expected: PASS (incluye los 3 kinds parametrizados en el ciclo HITL).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff check backend/app/graph/nodes.py && backend\.venv\Scripts\python -m ruff format backend/app/graph/nodes.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Full no-llm gate + commit**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (todo el backend no-llm verde, sin regresiones).

```bash
git add backend/app/graph/nodes.py backend/tests/test_nodes.py backend/tests/test_hitl_cycle.py
git commit -m "feat(graph): cablear cancel_appointment en nodos + copy de capacidades"
```

---

### Task 6: End-to-end con LLM real — `test_cancel_e2e_llm.py`

**Files:**
- Create: `backend/tests/test_cancel_e2e_llm.py` (nuevo)

**Interfaces:**
- Consumes: `build_graph(checkpointer=MemorySaver())`, `new_state`, `Command(resume=...)`, `db.create_appointment`, `db.list_active_practitioners`, `seed_demo` (todos existentes + Tasks 1–5 cableadas).
- Produces: nada (es verificación end-to-end).

- [ ] **Step 1: Write the e2e tests**

Crear `backend/tests/test_cancel_e2e_llm.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph
from app.graph.state import new_state


async def _seed_unique_client_with_appt(pid: str) -> tuple[str, str, str]:
    """Crea un cliente único con UN solo turno futuro cancelable → resolución no ambigua.
    Devuelve (full_name, client_id, appointment_id)."""
    from seed_demo import seed_demo

    await seed_demo()
    prac = (await db.list_active_practitioners(pid))[0]
    pool = await db.get_pool()
    full_name = "Casimiro Testcancel " + uuid4().hex[:6]
    client_id = await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text",
        pid,
        full_name,
    )
    start = datetime.now(UTC) + timedelta(days=3)
    appt = await db.create_appointment(
        pid, client_id, prac["id"], start, start + timedelta(minutes=30)
    )
    return full_name, client_id, appt["id"]


async def _status(appt_id: str) -> str:
    pool = await db.get_pool()
    return await pool.fetchval("SELECT status FROM appointments WHERE id = $1", appt_id)


@pytest.mark.llm
@pytest.mark.integration
async def test_cancel_confirm_sets_cancelado() -> None:
    pid = get_settings().practice_id
    full_name, client_id, appt_id = await _seed_unique_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-cancel-confirm"}}
        await graph.ainvoke(
            new_state(f"cancelá el turno de {full_name}", pid, "e2e-cancel-confirm"), config
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)  # se abrió la tarjeta
        assert snap.tasks[0].interrupts[0].value["kind"] == "cancel_appointment"  # clasificó bien
        await graph.ainvoke(Command(resume="confirm"), config)
        assert await _status(appt_id) == "cancelado"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)  # cascade → appointment


@pytest.mark.llm
@pytest.mark.integration
async def test_cancel_decline_leaves_it() -> None:
    pid = get_settings().practice_id
    full_name, client_id, appt_id = await _seed_unique_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-cancel-decline"}}
        await graph.ainvoke(
            new_state(f"cancelá el turno de {full_name}", pid, "e2e-cancel-decline"), config
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)
        await graph.ainvoke(Command(resume="cancel"), config)
        assert await _status(appt_id) == "programado"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", client_id)
```

- [ ] **Step 2: Run to verify it works against real models**

Requisitos: `docker compose up -d` + Ollama corriendo con `gemma4:12b` y `gemma4:e4b`.
Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_cancel_e2e_llm.py -m llm -q`
Expected: PASS (2 tests). Si el extractor `12b` no toma el nombre único de forma fiable, ajustar la frase (p. ej. usar un nombre más natural y agregar la fecha del turno) — los e2e `-m llm` toleran ajuste de prompt, como en Slices 4/5.

- [ ] **Step 3: Full llm gate (no-regresión de las otras tools)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m llm -q`
Expected: PASS (los e2e de create/log siguen verdes + los 2 nuevos de cancel).

- [ ] **Step 4: Lint**

Run: `backend\.venv\Scripts\python -m ruff check backend/tests/test_cancel_e2e_llm.py && backend\.venv\Scripts\python -m ruff format backend/tests/test_cancel_e2e_llm.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_cancel_e2e_llm.py
git commit -m "test(llm): e2e de cancel_appointment HITL"
```

---

## Final Gate (antes de cerrar el slice / mergear)

- [ ] `backend\.venv\Scripts\python -m ruff check backend && backend\.venv\Scripts\python -m ruff format --check backend` → limpio.
- [ ] `backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml` → limpio.
- [ ] `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → verde.
- [ ] `backend\.venv\Scripts\python -m pytest backend/tests -m llm -q` → verde (Ollama + Postgres).
- [ ] Frontend sin cambios → `npm --prefix frontend run test -- --run` + `npm --prefix frontend run lint` + `npm --prefix frontend run build` siguen verdes.
- [ ] **Smoke §2 en navegador**: "cancelá el turno de \<cliente\> del \<día\>" → abre tarjeta → Confirmar → ✅ + la fila queda `cancelado` en la DB; Cancelar → intacto; pedido ambiguo → abstiene listando; y "agendá un turno…" / "registrá que llamé a…" siguen abriendo tarjeta y escribiendo (no-regresión).
- [ ] Commits limpios, sin atribución a Claude.

---

## Self-Review (hecho al escribir el plan)

**1. Spec coverage** — cada sección del spec mapea a una task:
- Data layer (finder + writer) → Task 1.
- Resolver `resolve_single_appointment` + `AppointmentResolution` + heurística `when` + helper español → Task 2.
- Agente `cancel_agent` (`ProposedCancellation` + `propose_cancellation`, degradación de `when`, abstenciones) → Task 3.
- Registry (`_write_cancel`, `format_cancel_receipt`, `REGISTRY`, `WRITE_KINDS`, `CLASSIFY_PROMPT`) → Task 4.
- Nodos (copy de capacidades + cleanup `or {}`) → Task 5.
- Transporte/front: sin cambios (no requiere task; cubierto por no-regresión + smoke).
- Multi-tenant, HITL, idempotencia → cubiertos por tests en Tasks 1/4/5/6.
- e2e `-m llm` → Task 6.

**2. Placeholder scan** — sin TBD/TODO; todo el código (tests + impl) está completo y es ejecutable.

**3. Type consistency** — verificado: `find_cancellable_appointments(practice_id, client_id, *, now, limit)` y la clave `practitioner_full_name` se usan idénticas en Tasks 1→2→3; `resolve_single_appointment(practice_id, client, when, *, now, limit) -> AppointmentResolution(appointment, abstain_message, abstain_reason)` consistente entre 2 y 3; `proposed_action["params"]["appointment_id"]` producido en Task 3 y consumido por `_write_cancel` en Task 4; `format_cancel_receipt(params, row)` con `row["cancelled"]` producido por `_write_cancel`. `WRITE_KINDS` y `set(REGISTRY)` actualizados juntos en Task 4 (incluye el fix del test existente `test_registry_has_*`).
