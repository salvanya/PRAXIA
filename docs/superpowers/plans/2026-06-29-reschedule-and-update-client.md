# reschedule_appointment + update_client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar las write-tools 4ª (`reschedule_appointment` — reprogramar un turno) y 5ª (`update_client` — editar campos estructurados de un cliente) de Praxia sobre el registry del Slice 5, sin tocar router/transporte/front.

**Architecture:** Dos tools hermanas sobre el camino genérico de escritura. `reschedule_appointment` es la 2ª mutación y **reusa sin tocar** `resolve_single_appointment` (Slice 6) para resolver *cuál* turno; lo nuevo es extraer dos referencias temporales (turno actual + destino) y preservar la duración. `update_client` reusa `resolve_single_client` (Slice 5) y hace un `UPDATE … COALESCE` parcial sobre campos enumerados (teléfono/email/estado/dob); **excluye `notes`** (texto libre → PII sin redacción → diferido a Guardrails). HITL (`interrupt`/`resume`), nodos genéricos por `kind`, transporte SSE y `ConfirmCard` se reusan; el único toque fuera del registry es el copy de capacidades.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph (`interrupt`/`Command`, checkpointer Postgres), asyncpg, Pydantic, `langchain-ollama` (`ChatOllama` → `gemma4:12b` extracción / `gemma4:e4b` clasificador), pytest + pytest-asyncio.

## Global Constraints

Copiadas verbatim del spec / CLAUDE.md. Aplican a TODA tarea:

- **Inferencia 100% local por Ollama**; cero llamadas de red salientes fuera de Ollama/Postgres/Qdrant locales.
- **Costo $0**; sin dependencias nuevas (ninguna se necesita).
- **Aislamiento multi-tenant por `practice_id` SIEMPRE**: cada `UPDATE`/`SELECT` re-verifica `practice_id` en el `WHERE`; los resolvers ya filtran por `practice_id`.
- **Escrituras solo por tools parametrizadas (`$n`) + confirmación humana (HITL `interrupt`)**; nunca SQL de escritura del LLM. El LLM nunca produce UUIDs ni SQL: solo nombre + pistas + valores estructurados.
- **PII estructurada vs. texto libre**: `update_client` escribe teléfono/email/estado/dob (PII que ES el payload, vía tool parametrizada), pero **NO** `notes` (texto libre, diferido a Guardrails). **Los logs guardan `kind`+`client_id`+nombres de campos, NUNCA los valores en crudo** (CLAUDE.md §5).
- **Fail-closed**: cualquier excepción o ambigüedad (cliente, turno, o qué dato cambiar) → abstención, sin tarjeta, sin escribir. Nunca adivinar.
- **Gotcha de structured output (CLAUDE.md §4)**: `with_structured_output` del `e4b` devuelve `None` intermitente → el clasificador YA usa `ainvoke` + text-parse (no se cambia). Los **args tipados del extractor `12b` SÍ funcionan** con `with_structured_output`.
- **Commits LIMPIOS**: prohibido cualquier `Co-Authored-By: Claude`/atribución a Claude/Anthropic. Autor = el usuario.
- **ruff** `select = ["E","F","I","UP","B"]` (line-length 100): imports nuevos en archivos EXISTENTES van al TOP (E402); imports ordenados (I/isort). **Corré `ruff format` ANTES de `ruff check`** (format envuelve las líneas largas de código —dict/return/call— a ≤100 cols; las pragmas `# type: ignore` quedan exentas de E501, no hace falta envolverlas). **mypy SIEMPRE con `--config-file backend/pyproject.toml`** (`disallow_untyped_defs=true`: anotá todo; en tests usá `# type: ignore[no-untyped-def]` en fakes internos como hacen los tests previos).
- **Windows**: todo en UTC, etiquetado `(UTC)`. Para nombres de día de semana, mapa fijo español (ya en `resolvers.py`), nunca `strftime("%A")`.
- **Tests del backend**: `backend\.venv\Scripts\python -m pytest ...` desde la raíz del repo; los `integration` requieren `docker compose up -d`; los `-m llm` requieren además Ollama con `gemma4:12b` + `gemma4:e4b`.

---

## File Structure

- `backend/app/db.py` — **modify**: `+reschedule_appointment` (Task 1), `+get_client` `+update_client` (Task 3). Cambiar el import del tope a `from datetime import date, datetime` (Task 3).
- `backend/app/agents/reschedule_agent.py` — **create** (Task 2): `ProposedReschedule` + `propose_reschedule`.
- `backend/app/agents/update_client_agent.py` — **create** (Task 4): `ProposedClientUpdate` + `propose_update_client`.
- `backend/app/agents/write_tools.py` — **modify** (Task 5): `+_write_reschedule`/`format_reschedule_receipt`/`_write_update_client`/`format_update_client_receipt`, dos entradas en `REGISTRY`, `WRITE_KINDS` y `CLASSIFY_PROMPT` extendidos, `+date` en el import de datetime, `+` dos imports de agentes.
- `backend/app/graph/nodes.py` — **modify** (Task 6): solo copy de capacidades.
- `backend/app/agents/resolvers.py` — **SIN CAMBIOS** (reschedule reusa `resolve_single_appointment` tal cual).
- Tests: `test_db.py` (Tasks 1, 3), `test_reschedule_agent.py` (nuevo, Task 2), `test_update_client_agent.py` (nuevo, Task 4), `test_write_tools.py` (Task 5), `test_nodes.py` + `test_hitl_cycle.py` (Task 6), `test_reschedule_e2e_llm.py` + `test_update_client_e2e_llm.py` (nuevos, Task 7).

---

### Task 1: Data layer — `reschedule_appointment` en `db.py`

**Files:**
- Modify: `backend/app/db.py` (agregar una función tras `cancel_appointment`)
- Test: `backend/tests/test_db.py`

**Interfaces:**
- Consumes: `db.get_pool()`, `db.create_appointment(...)`, `db.list_active_practitioners(pid)`, helper `_new_client` (ya en `test_db.py`).
- Produces:
  - `async def reschedule_appointment(practice_id: str, appointment_id: str, new_start_at: datetime, new_end_at: datetime) -> dict[str, Any] | None` → fila `{id:str, start_at:datetime, end_at:datetime, status:str}` o `None` si no matcheó (otra práctica / no `programado`-`confirmado`).

- [ ] **Step 1: Write the failing test**

Agregar al final de `backend/tests/test_db.py` (reusa `_new_client` ya presente):

```python
@pytest.mark.integration
async def test_reschedule_moves_times_and_guards() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    prac = (await db.list_active_practitioners(pid))[0]
    cid = await _new_client(pid, "Reschedule Writer " + uuid4().hex[:6])
    now = datetime.now(UTC)
    try:
        start = now + timedelta(days=1)
        appt = await db.create_appointment(pid, cid, prac["id"], start, start + timedelta(minutes=30))
        new_start = now + timedelta(days=2)
        new_end = new_start + timedelta(minutes=30)

        row = await db.reschedule_appointment(pid, appt["id"], new_start, new_end)
        assert row is not None and row["status"] == "programado"
        assert row["start_at"] == new_start and row["end_at"] == new_end

        # guard de tenant: otra práctica no puede reprogramar
        assert await db.reschedule_appointment(str(uuid4()), appt["id"], new_start, new_end) is None

        # guard de estado: un turno cancelado no es reprogramable
        await db.cancel_appointment(pid, appt["id"])
        assert await db.reschedule_appointment(pid, appt["id"], new_start, new_end) is None
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_db.py::test_reschedule_moves_times_and_guards -q`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'reschedule_appointment'`.

- [ ] **Step 3: Implement the function**

Agregar en `backend/app/db.py` inmediatamente después de `cancel_appointment` (antes de `log_interaction`):

```python
async def reschedule_appointment(
    practice_id: str, appointment_id: str, new_start_at: datetime, new_end_at: datetime
) -> dict[str, Any] | None:
    """Tool de escritura parametrizada: mueve un turno a una nueva franja. Guard de tenant
    (practice_id) + de estado (solo programado/confirmado → idempotencia y TOCTOU). Devuelve la
    fila actualizada, o None si no matcheó (otra práctica, inexistente, o ya no reprogramable)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE appointments SET start_at = $3, end_at = $4
        WHERE id = $1 AND practice_id = $2 AND status IN ('programado', 'confirmado')
        RETURNING id::text, start_at, end_at, status
        """,
        appointment_id,
        practice_id,
        new_start_at,
        new_end_at,
    )
    return dict(row) if row is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_db.py -q`
Expected: PASS (todos, incluyendo los previos).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff format backend/app/db.py && backend\.venv\Scripts\python -m ruff check backend/app/db.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_db.py
git commit -m "feat(db): writer de reprogramacion de turnos"
```

---

### Task 2: Agente — `reschedule_agent.py` (`propose_reschedule`)

**Files:**
- Create: `backend/app/agents/reschedule_agent.py`
- Test: `backend/tests/test_reschedule_agent.py` (nuevo)

**Interfaces:**
- Consumes: `ProposalResult` (de `action_agent`), `resolve_single_client` + `resolve_single_appointment` (de `resolvers`), `db.find_clients_by_name` + `db.find_cancellable_appointments` (vía resolvers), `make_llm`, `get_settings`.
- Produces:
  - `class ProposedReschedule(BaseModel): client_name: str; current_when: str | None = None; new_start_at: str`
  - `async def propose_reschedule(question: str, practice_id: str, *, now: datetime, gen_llm: Any = None) -> ProposalResult` — `proposed_action = {"kind":"reschedule_appointment", "summary":<card>, "params":{"appointment_id","new_start_at","new_end_at","client_name","practitioner_name","old_start_at"}}`. Reasons de abstención: `extract_failed | client_not_found | client_ambiguous | client_missing | datetime_parse_failed | new_time_past | appointment_none | appointment_not_found | appointment_ambiguous`.

- [ ] **Step 1: Write the failing tests**

Crear `backend/tests/test_reschedule_agent.py`:

```python
from datetime import UTC, datetime

from app import db
from app.agents import reschedule_agent
from app.agents.reschedule_agent import ProposedReschedule

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedReschedule) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedReschedule) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _appt(aid="a1", start=datetime(2026, 7, 1, 10, 0, tzinfo=UTC), dur_min=30):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return {
        "id": aid,
        "start_at": start,
        "end_at": start + timedelta(minutes=dur_min),
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


async def test_happy_builds_action_and_preserves_duration(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt(dur_min=45)])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2026-07-03T15:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá el turno de Ana para el jueves 15", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None and pa["kind"] == "reschedule_appointment"
    assert pa["params"]["appointment_id"] == "a1"
    # duración preservada: 45 min
    s = datetime.fromisoformat(pa["params"]["new_start_at"])
    e = datetime.fromisoformat(pa["params"]["new_end_at"])
    assert (e - s).total_seconds() == 45 * 60
    assert "→" in pa["summary"] and "Ana López" in pa["summary"]


async def test_abstains_extract_fail() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await reschedule_agent.propose_reschedule("reprogramá", "pid", now=NOW, gen_llm=_LLM())
    assert result.abstained and result.reason == "extract_failed"


async def test_abstains_new_time_unparseable(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="no-es-fecha"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "datetime_parse_failed"


async def test_abstains_new_time_in_past(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2020-01-01T10:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "new_time_past"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [], [])
    llm = FakeGenLLM(ProposedReschedule(client_name="Zzz", new_start_at="2026-07-03T15:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found"


async def test_abstains_appointment_none(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2026-07-03T15:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "appointment_none"


async def test_unparseable_current_when_degrades(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(
        ProposedReschedule(
            client_name="Ana", current_when="no-es-fecha", new_start_at="2026-07-03T15:00:00"
        )
    )
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained  # current_when ilegible → None → resolver usa el único candidato
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["appointment_id"] == "a1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_reschedule_agent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.reschedule_agent'`.

- [ ] **Step 3: Implement the agent**

Crear `backend/app/agents/reschedule_agent.py`:

```python
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.agents.action_agent import ProposalResult
from app.agents.resolvers import resolve_single_appointment, resolve_single_client
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = "No pude entender la reprogramación. ¿Me decís el cliente y la nueva fecha y hora?"


class ProposedReschedule(BaseModel):
    client_name: str
    current_when: str | None = None
    new_start_at: str


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt(now: datetime) -> str:
    return (
        "Sos el asistente de agenda de una práctica profesional. A partir del pedido del usuario, "
        "extraé el cliente y la REPROGRAMACIÓN de un turno existente. La fecha y hora actuales son "
        f"{now.isoformat()} (UTC). 'new_start_at' es la NUEVA fecha/hora del turno (ABSOLUTA en ISO "
        "8601; resolvé 'mañana' o 'el jueves' contra la fecha actual) y es OBLIGATORIA. "
        "'current_when' es la fecha/hora ACTUAL del turno SOLO si se menciona, para saber cuál mover "
        "(en 'del martes al jueves', current_when es el martes y new_start_at el jueves); si solo se "
        "da una fecha, esa es new_start_at y current_when es null. client_name es la persona del turno."
    )


async def _extract(question: str, now: datetime, gen_llm: Any) -> ProposedReschedule | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedReschedule)
    try:
        result = await structured.ainvoke([("system", _system_prompt(now)), ("human", question)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedReschedule) else None


def _abstain(message: str, reason: str) -> ProposalResult:
    return ProposalResult(proposed_action=None, abstained=True, message=message, reason=reason)


def _parse_when(value: str | None) -> datetime | None:
    """Pista opcional: ilegible → None (se degrada a 'sin pista')."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _card_summary(
    client_name: str, practitioner_name: str, old_start: datetime, new_start: datetime
) -> str:
    return (
        f"Reprogramar el turno de {client_name} con {practitioner_name}: "
        f"{old_start.strftime('%d/%m %H:%M')} → {new_start.strftime('%d/%m %H:%M')} (UTC)"
    )


async def propose_reschedule(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    resolution = await resolve_single_client(
        practice_id, extracted.client_name, limit=settings.appt_name_match_limit
    )
    if resolution.client is None:
        return _abstain(resolution.abstain_message, resolution.abstain_reason)
    client = resolution.client

    # new_start_at es obligatorio: si no parsea, no se puede reprogramar (no se degrada).
    try:
        new_start = datetime.fromisoformat(extracted.new_start_at)
    except ValueError:
        return _abstain(
            "No entendí la nueva fecha y hora del turno. "
            "¿Me la indicás? (p. ej. 'el jueves a las 15:00').",
            "datetime_parse_failed",
        )
    if new_start.tzinfo is None:
        new_start = new_start.replace(tzinfo=UTC)
    if new_start < now:
        return _abstain(
            "Esa fecha ya pasó. Decime una fecha y hora futura para mover el turno.",
            "new_time_past",
        )

    current_when = _parse_when(extracted.current_when)
    appt_res = await resolve_single_appointment(
        practice_id, client, current_when, now=now, limit=settings.appt_name_match_limit
    )
    if appt_res.appointment is None:
        return _abstain(appt_res.abstain_message, appt_res.abstain_reason)
    appt = appt_res.appointment
    old_start = appt["start_at"]
    new_end = new_start + (appt["end_at"] - old_start)  # preserva la duración original

    params: dict[str, Any] = {
        "appointment_id": appt["id"],
        "new_start_at": new_start.isoformat(),
        "new_end_at": new_end.isoformat(),
        "client_name": client["full_name"],
        "practitioner_name": appt["practitioner_full_name"],
        "old_start_at": old_start.isoformat(),
    }
    proposed_action = {
        "kind": "reschedule_appointment",
        "summary": _card_summary(
            client["full_name"], appt["practitioner_full_name"], old_start, new_start
        ),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_reschedule_agent.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff format backend/app/agents/reschedule_agent.py && backend\.venv\Scripts\python -m ruff check backend/app/agents/reschedule_agent.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/reschedule_agent.py backend/tests/test_reschedule_agent.py
git commit -m "feat(agents): propose_reschedule (reschedule_agent)"
```

---

### Task 3: Data layer — `get_client` + `update_client` en `db.py`

**Files:**
- Modify: `backend/app/db.py` (import `date`; agregar dos funciones tras `log_interaction`)
- Test: `backend/tests/test_db.py`

**Interfaces:**
- Consumes: `db.get_pool()`, helper `_new_client`.
- Produces:
  - `async def get_client(practice_id: str, client_id: str) -> dict[str, Any] | None` → `{id:str, full_name:str, phone:str|None, email:str|None, status:str, dob:str|None}` o `None`.
  - `async def update_client(practice_id: str, client_id: str, *, phone: str | None = None, email: str | None = None, status: str | None = None, dob: date | None = None) -> dict[str, Any] | None` → fila actualizada (mismas claves que `get_client`) o `None`. `COALESCE`: setea solo lo provisto.

- [ ] **Step 1: Write the failing tests**

(a) En `backend/tests/test_db.py`, **cambiar** el import del tope de fechas para incluir `date`:

```python
from datetime import UTC, date, datetime, timedelta
```

(b) Agregar al final del archivo:

```python
@pytest.mark.integration
async def test_get_client_is_tenant_scoped() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    cid = await _new_client(pid, "Get Client " + uuid4().hex[:6])
    try:
        row = await db.get_client(pid, cid)
        assert row is not None and row["id"] == cid and row["status"] == "activo"
        assert await db.get_client(str(uuid4()), cid) is None  # otra práctica → None
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)


@pytest.mark.integration
async def test_update_client_partial_coalesce_and_guards() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    cid = await _new_client(pid, "Update Client " + uuid4().hex[:6])
    try:
        # solo phone: email/status/dob intactos (COALESCE)
        row = await db.update_client(pid, cid, phone="11-2233-4455")
        assert row is not None and row["phone"] == "11-2233-4455"
        assert row["email"] is None and row["status"] == "activo"

        # varios campos a la vez, incluyendo dob (date) y status enum
        row = await db.update_client(
            pid, cid, email="ana@x.com", status="baja", dob=date(1990, 5, 4)
        )
        assert row is not None
        assert row["email"] == "ana@x.com" and row["status"] == "baja"
        assert row["dob"] == "1990-05-04" and row["phone"] == "11-2233-4455"  # phone se mantuvo

        # guard de tenant: otra práctica → None y sin efecto
        assert await db.update_client(str(uuid4()), cid, phone="99") is None
        assert (await db.get_client(pid, cid))["phone"] == "11-2233-4455"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_db.py::test_get_client_is_tenant_scoped backend/tests/test_db.py::test_update_client_partial_coalesce_and_guards -q`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'get_client'`.

- [ ] **Step 3: Implement the functions**

(a) En `backend/app/db.py`, cambiar el import del tope:

```python
from datetime import date, datetime
```

(b) Agregar al final del archivo (después de `log_interaction`):

```python
async def get_client(practice_id: str, client_id: str) -> dict[str, Any] | None:
    """Lee un cliente scopeado por práctica (para el antes→después de update_client)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id::text, full_name, phone, email, status, dob::text
        FROM clients WHERE id = $1 AND practice_id = $2
        """,
        client_id,
        practice_id,
    )
    return dict(row) if row is not None else None


async def update_client(
    practice_id: str,
    client_id: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    status: str | None = None,
    dob: date | None = None,
) -> dict[str, Any] | None:
    """Tool de escritura parametrizada: actualiza campos ESTRUCTURADOS del cliente. COALESCE
    setea solo lo provisto (un None mantiene el valor actual, no borra). Guard de tenant
    (practice_id). El CHECK del schema valida el enum de status. Devuelve la fila actualizada,
    o None si el cliente es de otra práctica / inexistente. NO toca `notes` (texto libre)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE clients SET
            phone = COALESCE($3, phone),
            email = COALESCE($4, email),
            status = COALESCE($5, status),
            dob = COALESCE($6, dob)
        WHERE id = $1 AND practice_id = $2
        RETURNING id::text, full_name, phone, email, status, dob::text
        """,
        client_id,
        practice_id,
        phone,
        email,
        status,
        dob,
    )
    return dict(row) if row is not None else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_db.py -q`
Expected: PASS (todos).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff format backend/app/db.py && backend\.venv\Scripts\python -m ruff check backend/app/db.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_db.py
git commit -m "feat(db): get_client + update_client (UPDATE parcial COALESCE)"
```

---

### Task 4: Agente — `update_client_agent.py` (`propose_update_client`)

**Files:**
- Create: `backend/app/agents/update_client_agent.py`
- Test: `backend/tests/test_update_client_agent.py` (nuevo)

**Interfaces:**
- Consumes: `ProposalResult` (de `action_agent`), `resolve_single_client` (de `resolvers`), `db.find_clients_by_name` (vía resolver) + `db.get_client` (Task 3), `make_llm`, `get_settings`.
- Produces:
  - `class ProposedClientUpdate(BaseModel): client_name: str; phone: str | None = None; email: str | None = None; status: Literal["activo","inactivo","baja"] | None = None; dob: str | None = None`
  - `async def propose_update_client(question: str, practice_id: str, *, now: datetime, gen_llm: Any = None) -> ProposalResult` (`now` por uniformidad del dispatch; no se usa). `proposed_action = {"kind":"update_client", "summary":<antes→después>, "params":{"client_id","client_name", <solo campos cambiados>}}`. Reasons: `extract_failed | client_not_found | client_ambiguous | client_missing | no_fields`.

- [ ] **Step 1: Write the failing tests**

Crear `backend/tests/test_update_client_agent.py`:

```python
from datetime import UTC, datetime

from app import db
from app.agents import update_client_agent
from app.agents.update_client_agent import ProposedClientUpdate

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedClientUpdate) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedClientUpdate) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _patch(monkeypatch, clients, current):  # type: ignore[no-untyped-def]
    async def _find(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    async def _get(practice_id, client_id):  # type: ignore[no-untyped-def]
        return current

    monkeypatch.setattr(db, "find_clients_by_name", _find)
    monkeypatch.setattr(db, "get_client", _get)


_CLIENT = [{"id": "c1", "full_name": "Ana López"}]
_CURRENT = {"id": "c1", "full_name": "Ana López", "phone": "11-1111-1111", "email": None, "status": "activo", "dob": None}


async def test_happy_single_field(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", phone="11-2233-4455"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Ana a 11-2233-4455", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None and pa["kind"] == "update_client"
    assert pa["params"]["client_id"] == "c1" and pa["params"]["phone"] == "11-2233-4455"
    assert "email" not in pa["params"]  # solo el campo cambiado
    assert "11-1111-1111" in pa["summary"] and "11-2233-4455" in pa["summary"]  # antes→después


async def test_happy_multi_field(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", email="ana@x.com", status="baja"))
    result = await update_client_agent.propose_update_client(
        "actualizá el email de Ana y dala de baja", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    p = result.proposed_action["params"]
    assert p["email"] == "ana@x.com" and p["status"] == "baja" and "phone" not in p


async def test_abstains_extract_fail() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await update_client_agent.propose_update_client(
        "cambiá", "pid", now=NOW, gen_llm=_LLM()
    )
    assert result.abstained and result.reason == "extract_failed"


async def test_abstains_no_fields(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana"))  # ningún campo
    result = await update_client_agent.propose_update_client(
        "tocá algo de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "no_fields"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [], None)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Zzz", phone="123"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found"


async def test_invalid_dob_dropped_keeps_other(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", phone="999", dob="no-es-fecha"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    p = result.proposed_action["params"]
    assert p["phone"] == "999" and "dob" not in p  # dob ilegible se descarta


async def test_invalid_dob_alone_abstains(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", dob="no-es-fecha"))
    result = await update_client_agent.propose_update_client(
        "cambiá la fecha de nacimiento de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "no_fields"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_update_client_agent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agents.update_client_agent'`.

- [ ] **Step 3: Implement the agent**

Crear `backend/app/agents/update_client_agent.py`:

```python
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from app import db
from app.agents.action_agent import ProposalResult
from app.agents.resolvers import resolve_single_client
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude entender qué dato del cliente cambiar. ¿Me decís el cliente y el dato "
    "(teléfono, email, estado o fecha de nacimiento)?"
)
_FIELD_LABELS = {
    "phone": "teléfono",
    "email": "email",
    "status": "estado",
    "dob": "fecha de nacimiento",
}


class ProposedClientUpdate(BaseModel):
    client_name: str
    phone: str | None = None
    email: str | None = None
    status: Literal["activo", "inactivo", "baja"] | None = None
    dob: str | None = None


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt() -> str:
    return (
        "Sos el asistente de datos de clientes de una práctica profesional. A partir del pedido "
        "del usuario, extraé el cliente y SOLO los datos a cambiar entre: phone (teléfono), email, "
        "status ('activo'/'inactivo'/'baja') y dob (fecha de nacimiento, formato YYYY-MM-DD). Si un "
        "dato no se menciona, dejalo en null. 'dar de baja' → status='baja'; 'reactivar' → "
        "status='activo'. No inventes valores. No extraigas notas ni texto libre."
    )


async def _extract(question: str, gen_llm: Any) -> ProposedClientUpdate | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedClientUpdate)
    try:
        result = await structured.ainvoke([("system", _system_prompt()), ("human", question)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedClientUpdate) else None


def _abstain(message: str, reason: str) -> ProposalResult:
    return ProposalResult(proposed_action=None, abstained=True, message=message, reason=reason)


def _card_summary(client_name: str, changes: dict[str, str], before: dict[str, Any]) -> str:
    parts = [
        f"{_FIELD_LABELS[field]} {before.get(field) or '—'} → {new_value}"
        for field, new_value in changes.items()
    ]
    return f"Actualizar {client_name}: " + "; ".join(parts)


async def propose_update_client(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None
) -> ProposalResult:
    # `now` se acepta por uniformidad del dispatch (nodes.py lo pasa siempre); no se usa acá.
    settings = get_settings()
    extracted = await _extract(question, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    resolution = await resolve_single_client(
        practice_id, extracted.client_name, limit=settings.appt_name_match_limit
    )
    if resolution.client is None:
        return _abstain(resolution.abstain_message, resolution.abstain_reason)
    client = resolution.client

    changes: dict[str, str] = {}
    if extracted.phone:
        changes["phone"] = extracted.phone
    if extracted.email:
        changes["email"] = extracted.email
    if extracted.status:
        changes["status"] = extracted.status
    if extracted.dob:
        try:
            date.fromisoformat(extracted.dob)
            changes["dob"] = extracted.dob
        except ValueError:
            pass  # dob ilegible → se descarta (degrada); si no queda nada, abstiene abajo
    if not changes:
        return _abstain(
            "¿Qué dato querés cambiar? Puedo teléfono, email, estado o fecha de nacimiento.",
            "no_fields",
        )

    before = await db.get_client(practice_id, client["id"]) or {}
    params: dict[str, Any] = {
        "client_id": client["id"],
        "client_name": client["full_name"],
        **changes,
    }
    proposed_action = {
        "kind": "update_client",
        "summary": _card_summary(client["full_name"], changes, before),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_update_client_agent.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff format backend/app/agents/update_client_agent.py && backend\.venv\Scripts\python -m ruff check backend/app/agents/update_client_agent.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/update_client_agent.py backend/tests/test_update_client_agent.py
git commit -m "feat(agents): propose_update_client (update_client_agent)"
```

---

### Task 5: Registry + clasificador — `write_tools.py`

**Files:**
- Modify: `backend/app/agents/write_tools.py`
- Test: `backend/tests/test_write_tools.py` (agregar casos + actualizar dos existentes)

**Interfaces:**
- Consumes: `propose_reschedule` (Task 2), `propose_update_client` (Task 4), `db.reschedule_appointment` (Task 1), `db.update_client` (Task 3), `WriteTool`/`REGISTRY`/`WRITE_KINDS`/`classify_write_action` (existentes).
- Produces:
  - `async def _write_reschedule(practice_id, params) -> dict` → `{"rescheduled": True, **row}` o `{"rescheduled": False}`.
  - `def format_reschedule_receipt(params, row) -> str`.
  - `async def _write_update_client(practice_id, params) -> dict` → `{"updated": True, **row}` o `{"updated": False}`.
  - `def format_update_client_receipt(params, row) -> str`.
  - `REGISTRY["reschedule_appointment"]` y `REGISTRY["update_client"]`; `WRITE_KINDS` incluye ambos.

- [ ] **Step 1: Update existing tests + write new failing tests**

En `backend/tests/test_write_tools.py`:

(a) **Reemplazar** `test_registry_has_all_tools` por:

```python
def test_registry_has_all_tools() -> None:
    assert set(REGISTRY) == {
        "create_appointment",
        "log_interaction",
        "cancel_appointment",
        "reschedule_appointment",
        "update_client",
    }
    for kind, tool in REGISTRY.items():
        assert tool.kind == kind
        assert tool.cancel_message
```

(b) En `test_classify_returns_kind`, **cambiar** el caso unsupported (reprogramar ahora es una tool; usar una frase que SIGUE siendo unsupported):

```python
    assert (
        await classify_write_action("facturá la sesión", llm=FakeSeqLLM("unsupported"))
        == "unsupported"
    )
```

(c) **Agregar** al final del archivo:

```python
async def test_classify_routes_reschedule_and_update_client() -> None:
    assert (
        await classify_write_action(
            "reprogramá el turno de Ana", llm=FakeSeqLLM("reschedule_appointment")
        )
        == "reschedule_appointment"
    )
    assert (
        await classify_write_action(
            "cambiá el teléfono de Ana", llm=FakeSeqLLM("update_client")
        )
        == "update_client"
    )


async def test_write_reschedule_adapter(monkeypatch) -> None:
    captured: dict = {}

    async def _fake(practice_id, appointment_id, new_start_at, new_end_at):  # type: ignore[no-untyped-def]
        captured.update(appointment_id=appointment_id, new_start_at=new_start_at, new_end_at=new_end_at)
        return {"id": appointment_id, "status": "programado", "start_at": new_start_at, "end_at": new_end_at}

    monkeypatch.setattr(write_tools.db, "reschedule_appointment", _fake)
    params = {
        "appointment_id": "a1",
        "new_start_at": "2026-07-03T15:00:00+00:00",
        "new_end_at": "2026-07-03T15:30:00+00:00",
        "client_name": "Ana López",
    }
    row = await write_tools._write_reschedule("pid", params)
    assert row["rescheduled"] is True
    assert captured["new_start_at"] == datetime(2026, 7, 3, 15, 0, tzinfo=UTC)  # ISO→datetime
    assert "client_name" not in captured


async def test_write_reschedule_adapter_handles_none(monkeypatch) -> None:
    async def _fake(practice_id, appointment_id, new_start_at, new_end_at):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(write_tools.db, "reschedule_appointment", _fake)
    row = await write_tools._write_reschedule(
        "pid",
        {"appointment_id": "a1", "new_start_at": "2026-07-03T15:00:00+00:00", "new_end_at": "2026-07-03T15:30:00+00:00"},
    )
    assert row == {"rescheduled": False}


def test_reschedule_receipt_ok_and_not_ok() -> None:
    params = {
        "client_name": "Ana López",
        "practitioner_name": "Dra. Gómez",
        "new_start_at": "2026-07-03T15:00:00+00:00",
    }
    ok = write_tools.format_reschedule_receipt(params, {"rescheduled": True})
    assert "✅" in ok and "Ana López" in ok and "Dra. Gómez" in ok
    bad = write_tools.format_reschedule_receipt(params, {"rescheduled": False})
    assert "⚠️" in bad


async def test_write_update_client_adapter(monkeypatch) -> None:
    captured: dict = {}

    async def _fake(practice_id, client_id, *, phone, email, status, dob):  # type: ignore[no-untyped-def]
        captured.update(client_id=client_id, phone=phone, email=email, status=status, dob=dob)
        return {"id": client_id, "full_name": "Ana López", "phone": phone, "email": email, "status": status, "dob": None}

    monkeypatch.setattr(write_tools.db, "update_client", _fake)
    params = {"client_id": "c1", "client_name": "Ana López", "phone": "11-2233-4455", "status": "baja"}
    row = await write_tools._write_update_client("pid", params)
    assert row["updated"] is True
    assert captured["phone"] == "11-2233-4455" and captured["status"] == "baja"
    assert captured["email"] is None and captured["dob"] is None  # no provistos → None
    assert "client_name" not in captured


async def test_write_update_client_adapter_handles_none(monkeypatch) -> None:
    async def _fake(practice_id, client_id, *, phone, email, status, dob):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(write_tools.db, "update_client", _fake)
    row = await write_tools._write_update_client("pid", {"client_id": "c1", "phone": "9"})
    assert row == {"updated": False}


def test_update_client_receipt_lists_changed_fields() -> None:
    params = {"client_id": "c1", "phone": "11-2233-4455", "status": "baja"}
    ok = write_tools.format_update_client_receipt(params, {"updated": True, "full_name": "Ana López"})
    assert "✅" in ok and "Ana López" in ok
    assert "teléfono" in ok and "11-2233-4455" in ok and "estado" in ok
    assert "email" not in ok  # no cambió → no se lista
    bad = write_tools.format_update_client_receipt(params, {"updated": False})
    assert "⚠️" in bad
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py -q`
Expected: FAIL — `test_registry_has_all_tools` falla (faltan los dos kinds) y `_write_reschedule`/`format_reschedule_receipt`/`_write_update_client`/`format_update_client_receipt` no existen (`AttributeError`).

- [ ] **Step 3: Implement registry + classifier changes**

En `backend/app/agents/write_tools.py`:

(a) Cambiar el import de datetime del tope para incluir `date`:

```python
from datetime import date, datetime
```

(b) Agregar los imports de los nuevos agentes (junto a los otros, ordenados alfabéticamente):

```python
from app.agents.reschedule_agent import propose_reschedule
from app.agents.update_client_agent import propose_update_client
```

(el bloque queda: `action_agent`, `cancel_agent`, `interaction_agent`, `reschedule_agent`, `update_client_agent`.)

(c) Agregar las funciones de las tools (después del bloque de `cancel_appointment`, antes de `REGISTRY`):

```python
# ---- reschedule_appointment ----
async def _write_reschedule(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = await db.reschedule_appointment(
        practice_id,
        params["appointment_id"],
        datetime.fromisoformat(params["new_start_at"]),
        datetime.fromisoformat(params["new_end_at"]),
    )
    return {"rescheduled": True, **row} if row is not None else {"rescheduled": False}


def format_reschedule_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("rescheduled"):
        return (
            "⚠️ No pude reprogramar el turno: ya no estaba disponible "
            "(puede haberse cancelado o atendido)."
        )
    start = datetime.fromisoformat(params["new_start_at"])
    return (
        f"✅ Turno reprogramado: {params['client_name']} con {params['practitioner_name']} "
        f"→ {start.strftime('%d/%m %H:%M')} (UTC)."
    )


# ---- update_client ----
_CLIENT_FIELD_LABELS = {
    "phone": "teléfono",
    "email": "email",
    "status": "estado",
    "dob": "fecha de nacimiento",
}


async def _write_update_client(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    dob = params.get("dob")
    row = await db.update_client(
        practice_id,
        params["client_id"],
        phone=params.get("phone"),
        email=params.get("email"),
        status=params.get("status"),
        dob=date.fromisoformat(dob) if dob else None,
    )
    return {"updated": True, **row} if row is not None else {"updated": False}


def format_update_client_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("updated"):
        return "⚠️ No pude actualizar al cliente: no lo encontré."
    campos = [
        f"{_CLIENT_FIELD_LABELS[f]} → {params[f]}"
        for f in ("phone", "email", "status", "dob")
        if params.get(f)
    ]
    return f"✅ Datos actualizados de {row['full_name']}: " + "; ".join(campos) + "."
```

(d) Agregar las dos entradas al `REGISTRY` (dentro del dict literal, después de `cancel_appointment`):

```python
    "reschedule_appointment": WriteTool(
        kind="reschedule_appointment",
        propose=propose_reschedule,
        write=_write_reschedule,
        format_receipt=format_reschedule_receipt,
        cancel_message="Listo, dejé el turno como estaba.",
    ),
    "update_client": WriteTool(
        kind="update_client",
        propose=propose_update_client,
        write=_write_update_client,
        format_receipt=format_update_client_receipt,
        cancel_message="Listo, no cambié los datos del cliente.",
    ),
```

(e) Extender `WRITE_KINDS`:

```python
WRITE_KINDS: tuple[str, ...] = (
    "create_appointment",
    "log_interaction",
    "cancel_appointment",
    "reschedule_appointment",
    "update_client",
    "unsupported",
)
```

(f) Reemplazar `CLASSIFY_PROMPT` por la versión extendida (saca reprogramar/dar-de-baja de unsupported; contrasta por verbo y por objeto turno-vs-cliente):

```python
CLASSIFY_PROMPT = (
    "Sos el despachador de acciones de escritura de un CRM de prácticas profesionales. "
    "El usuario pidió ejecutar UNA acción que modifica datos. Clasificá QUÉ acción es:\n"
    "- create_appointment: agendar/crear un turno NUEVO. "
    'Ej: "agendá un turno para Ana mañana 10".\n'
    "- log_interaction: registrar/anotar una interacción YA OCURRIDA con un cliente "
    "(sesión, llamada, email, nota, mensaje). "
    'Ej: "registrá que llamé a Ana".\n'
    "- cancel_appointment: cancelar/anular un turno EXISTENTE. "
    'Ej: "cancelá el turno de Juan".\n'
    "- reschedule_appointment: REPROGRAMAR/MOVER/cambiar la fecha u hora de un turno EXISTENTE "
    "(el turno sigue existiendo, cambia CUÁNDO). "
    'Ej: "reprogramá el turno de Juan para el jueves", "movés la cita de Ana a las 15", '
    '"cambiá el turno de Pedro al lunes 11".\n'
    "- update_client: editar DATOS del CLIENTE (teléfono, email, estado activo/inactivo/baja, "
    "fecha de nacimiento). "
    'Ej: "cambiá el teléfono de Ana", "actualizá el email de Juan", "dá de baja a Pedro".\n'
    "- unsupported: cualquier OTRA acción de escritura (facturar; agregar/editar una NOTA o texto "
    "libre de un cliente; borrar registros). "
    'Ej: "agregá una nota sobre Juan", "facturá la sesión de Ana".\n'
    "Respondé solo con la opción."
)
```

> El mecanismo de `classify_write_action` (`ainvoke` + text-parse + retry + fallback `unsupported`) NO se toca: el match exacto/substring sigue funcionando porque los 6 kinds no se solapan como substrings.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py -q`
Expected: PASS (todos).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff format backend/app/agents/write_tools.py && backend\.venv\Scripts\python -m ruff check backend/app/agents/write_tools.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/write_tools.py backend/tests/test_write_tools.py
git commit -m "feat(write-tools): registrar reschedule_appointment + update_client + clasificador"
```

---

### Task 6: Cablear en el grafo — copy de capacidades + cobertura HITL

**Files:**
- Modify: `backend/app/graph/nodes.py`
- Test: `backend/tests/test_nodes.py`, `backend/tests/test_hitl_cycle.py`

**Interfaces:**
- Consumes: nodos genéricos `propose_action_node`/`confirm_action_node` (su dispatch por `kind` no cambia), `WriteTool`, `REGISTRY`.
- Produces: mensaje de capacidades que incluye "reprogramar" y "actualizar datos de clientes".

- [ ] **Step 1: Update tests (failing)**

(a) En `backend/tests/test_nodes.py`, en `test_propose_action_unsupported_emits_capabilities`, **agregar** dos aserciones tras las existentes (las previas — `"agendar turnos"`, `"registrar interacciones"`, `"cancelar turnos"` — se mantienen):

```python
    assert "reprogramar" in tokens and "actualizar datos de clientes" in tokens
```

(b) En `backend/tests/test_hitl_cycle.py`, **agregar** las constantes junto a `APPOINTMENT`/`INTERACTION`/`CANCELLATION`:

```python
RESCHEDULE = {
    "kind": "reschedule_appointment",
    "summary": "Reprogramar el turno de Ana López con Dra. Gómez: 01/07 10:00 → 03/07 15:00 (UTC)",
    "params": {"appointment_id": "a1"},
}
UPDATE_CLIENT = {
    "kind": "update_client",
    "summary": "Actualizar Ana López: teléfono 11-1111-1111 → 11-2233-4455",
    "params": {"client_id": "c1"},
}
```

y **agregar** `("reschedule_appointment", RESCHEDULE)` y `("update_client", UPDATE_CLIENT)` a las listas `@pytest.mark.parametrize` de **ambos** tests (`test_confirm_writes_exactly_once` y `test_cancel_writes_nothing`):

```python
@pytest.mark.parametrize(
    "kind,action",
    [
        ("create_appointment", APPOINTMENT),
        ("log_interaction", INTERACTION),
        ("cancel_appointment", CANCELLATION),
        ("reschedule_appointment", RESCHEDULE),
        ("update_client", UPDATE_CLIENT),
    ],
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_nodes.py::test_propose_action_unsupported_emits_capabilities -q`
Expected: FAIL — `assert "reprogramar" in tokens` falla (el copy actual no lo incluye). (Los params nuevos de `test_hitl_cycle` ya pasan: el dispatch es genérico y usa un `WriteTool` fake.)

- [ ] **Step 3: Implement the node change**

En `backend/app/graph/nodes.py`, en `propose_action_node`, reemplazar el mensaje de capacidades. **Mantené `"agendar turnos"`, `"cancelar turnos"` y `"registrar interacciones"` como substrings contiguos** (otros tests los verifican):

```python
        msg = (
            "Por ahora puedo agendar turnos, reprogramar o cancelar turnos, "
            "registrar interacciones o actualizar datos de clientes "
            "(teléfono, email, estado). ¿Qué necesitás?"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_nodes.py backend/tests/test_hitl_cycle.py -q`
Expected: PASS (incluye los 5 kinds parametrizados en el ciclo HITL).

- [ ] **Step 5: Lint + type check**

Run: `backend\.venv\Scripts\python -m ruff format backend/app/graph/nodes.py && backend\.venv\Scripts\python -m ruff check backend/app/graph/nodes.py && backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: sin errores.

- [ ] **Step 6: Full no-llm gate + commit**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (todo el backend no-llm verde, sin regresiones de las tres tools previas).

```bash
git add backend/app/graph/nodes.py backend/tests/test_nodes.py backend/tests/test_hitl_cycle.py
git commit -m "feat(graph): copy de capacidades con reprogramar + actualizar cliente"
```

---

### Task 7: End-to-end con LLM real — reschedule + update_client

**Files:**
- Create: `backend/tests/test_reschedule_e2e_llm.py` (nuevo)
- Create: `backend/tests/test_update_client_e2e_llm.py` (nuevo)

**Interfaces:**
- Consumes: `build_graph(checkpointer=MemorySaver())`, `new_state`, `Command(resume=...)`, `db.create_appointment`, `db.list_active_practitioners`, `db.get_client`, `seed_demo` (todos existentes + Tasks 1–6 cableadas).
- Produces: nada (verificación end-to-end).

- [ ] **Step 1: Write the reschedule e2e**

Crear `backend/tests/test_reschedule_e2e_llm.py`:

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


async def _seed_client_with_appt(pid: str) -> tuple[str, str, str, datetime]:
    """Cliente único con UN turno futuro → resolución no ambigua. Devuelve (full_name, cid, aid, start)."""
    from seed_demo import seed_demo

    await seed_demo()
    prac = (await db.list_active_practitioners(pid))[0]
    pool = await db.get_pool()
    full_name = "Casimiro Testresched " + uuid4().hex[:6]
    cid = await pool.fetchval(
        "INSERT INTO clients (practice_id, full_name) VALUES ($1, $2) RETURNING id::text",
        pid,
        full_name,
    )
    start = datetime.now(UTC) + timedelta(days=5)
    appt = await db.create_appointment(pid, cid, prac["id"], start, start + timedelta(minutes=30))
    return full_name, cid, appt["id"], start


async def _row(appt_id: str) -> tuple[datetime, str]:
    pool = await db.get_pool()
    r = await pool.fetchrow("SELECT start_at, status FROM appointments WHERE id = $1", appt_id)
    return r["start_at"], r["status"]


@pytest.mark.llm
@pytest.mark.integration
async def test_reschedule_confirm_moves_appointment() -> None:
    pid = get_settings().practice_id
    full_name, cid, aid, original_start = await _seed_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-resched-confirm"}}
        await graph.ainvoke(
            new_state(f"reprogramá el turno de {full_name} para mañana a las 15:00", pid, "e2e-resched-confirm"),
            config,
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)
        assert snap.tasks[0].interrupts[0].value["kind"] == "reschedule_appointment"  # clasificó bien
        await graph.ainvoke(Command(resume="confirm"), config)
        new_start, status = await _row(aid)
        assert status == "programado"  # estado intacto
        assert new_start != original_start  # se movió
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)  # cascade → appointment


@pytest.mark.llm
@pytest.mark.integration
async def test_reschedule_decline_leaves_it() -> None:
    pid = get_settings().practice_id
    full_name, cid, aid, original_start = await _seed_client_with_appt(pid)
    try:
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "e2e-resched-decline"}}
        await graph.ainvoke(
            new_state(f"reprogramá el turno de {full_name} para mañana a las 15:00", pid, "e2e-resched-decline"),
            config,
        )
        await graph.ainvoke(Command(resume="cancel"), config)
        new_start, status = await _row(aid)
        assert new_start == original_start and status == "programado"
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
```

- [ ] **Step 2: Write the update_client e2e**

Crear `backend/tests/test_update_client_e2e_llm.py`:

```python
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
        "INSERT INTO clients (practice_id, full_name, phone) VALUES ($1, $2, $3) RETURNING id::text",
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
            new_state(f"cambiá el teléfono de {full_name} a 11-9999-0000", pid, "e2e-upd-confirm"),
            config,
        )
        snap = await graph.aget_state(config)
        assert snap.next == ("confirm_action",)
        assert snap.tasks[0].interrupts[0].value["kind"] == "update_client"  # clasificó bien
        await graph.ainvoke(Command(resume="confirm"), config)
        phone = (await db.get_client(pid, cid))["phone"]
        assert phone != "11-0000-0000" and phone is not None  # se cambió
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
            new_state(f"cambiá el teléfono de {full_name} a 11-9999-0000", pid, "e2e-upd-decline"),
            config,
        )
        await graph.ainvoke(Command(resume="cancel"), config)
        assert (await db.get_client(pid, cid))["phone"] == "11-0000-0000"  # intacto
    finally:
        pool = await db.get_pool()
        await pool.execute("DELETE FROM clients WHERE id = $1", cid)
```

- [ ] **Step 3: Run to verify against real models**

Requisitos: `docker compose up -d` + Ollama con `gemma4:12b` y `gemma4:e4b`.
Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_reschedule_e2e_llm.py backend/tests/test_update_client_e2e_llm.py -m llm -q`
Expected: PASS (4 tests). Si el extractor `12b` no toma fiablemente el nombre único o la fecha/teléfono, ajustar la frase (los e2e `-m llm` toleran ajuste de prompt, como en Slices 4/5/6).

- [ ] **Step 4: Full llm gate (no-regresión de las otras tools)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m llm -q`
Expected: PASS (los e2e de create/log/cancel siguen verdes + los 4 nuevos).

- [ ] **Step 5: Lint + commit**

Run: `backend\.venv\Scripts\python -m ruff format backend/tests/test_reschedule_e2e_llm.py backend/tests/test_update_client_e2e_llm.py && backend\.venv\Scripts\python -m ruff check backend/tests/test_reschedule_e2e_llm.py backend/tests/test_update_client_e2e_llm.py`
Expected: sin errores.

```bash
git add backend/tests/test_reschedule_e2e_llm.py backend/tests/test_update_client_e2e_llm.py
git commit -m "test(llm): e2e de reschedule_appointment + update_client HITL"
```

---

## Final Gate (antes de cerrar el slice / mergear)

- [ ] `backend\.venv\Scripts\python -m ruff check backend && backend\.venv\Scripts\python -m ruff format --check backend` → limpio.
- [ ] `backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml` → limpio.
- [ ] `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → verde.
- [ ] `backend\.venv\Scripts\python -m pytest backend/tests -m llm -q` → verde (Ollama + Postgres).
- [ ] Frontend sin cambios → `npm --prefix frontend run test -- --run` + `npm --prefix frontend run lint` + `npm --prefix frontend run build` siguen verdes.
- [ ] **Smoke §2 en navegador**: "reprogramá el turno de \<cliente\> para \<fecha futura\>" → abre tarjeta (viejo→nuevo) → Confirmar → ✅ + `start_at`/`end_at` cambian en la DB; "cambiá el teléfono de \<cliente\> a \<nro\>" → tarjeta antes→después → Confirmar → ✅ + `phone` cambia en la DB; Cancelar → intacto; pedido ambiguo → abstiene listando; y "agendá un turno…" / "registrá que llamé a…" / "cancelá el turno de…" siguen funcionando (no-regresión).
- [ ] Commits limpios, sin atribución a Claude.

---

## Self-Review (hecho al escribir el plan)

**1. Spec coverage** — cada sección del spec mapea a una task:
- Data layer reschedule (`reschedule_appointment`) → Task 1.
- Agente `reschedule_agent` (extracción doble fecha, preservar duración, rechazar pasado, degradar current_when, abstenciones) → Task 2.
- Data layer cliente (`get_client`, `update_client` COALESCE) → Task 3.
- Agente `update_client_agent` (campos estructurados, dob inválida descarta, no_fields, antes→después) → Task 4.
- Registry + clasificador (adapters, receipts, `REGISTRY`, `WRITE_KINDS`, `CLASSIFY_PROMPT`) → Task 5.
- Nodos (copy de capacidades) + cobertura HITL de ambos kinds → Task 6.
- e2e `-m llm` (ambas tools, confirm + decline) → Task 7.
- `resolvers.py` sin cambios (reschedule reusa `resolve_single_appointment`): verificado por los tests de agente (Task 2) y e2e (Task 7).
- Multi-tenant, idempotencia/TOCTOU, sin `notes`/PII-en-logs → cubiertos por tests en Tasks 1/3/5 + Global Constraints.

**2. Placeholder scan** — sin TBD/TODO; todo el código (tests + impl) está completo y ejecutable.

**3. Type consistency** — verificado:
- `reschedule_appointment(practice_id, appointment_id, new_start_at: datetime, new_end_at: datetime) -> dict|None` producido en Task 1, consumido por `_write_reschedule` en Task 5 (parsea ISO→datetime).
- `proposed_action["params"]` de reschedule (`appointment_id`, `new_start_at`, `new_end_at`, `client_name`, `practitioner_name`, `old_start_at`) producido en Task 2, consumido por `_write_reschedule`/`format_reschedule_receipt` en Task 5.
- `get_client`/`update_client` (claves `phone/email/status/dob`, `dob: date`) producidas en Task 3; `update_client` consumido por `_write_update_client` en Task 5 (parsea ISO→`date`); `get_client` consumido por `propose_update_client` en Task 4.
- `proposed_action["params"]` de update_client (`client_id`, `client_name`, + campos cambiados) producido en Task 4, consumido por `_write_update_client`/`format_update_client_receipt` en Task 5.
- `WRITE_KINDS` y `set(REGISTRY)` actualizados juntos en Task 5 (incluye el fix de `test_registry_has_all_tools` y del caso unsupported de `test_classify_returns_kind`).
- Copy de capacidades: el nuevo string mantiene `"agendar turnos"`, `"cancelar turnos"`, `"registrar interacciones"` contiguos (Task 6) → `test_propose_action_classifier_exception_is_fail_closed` (no tocado) sigue verde; el test de capacidades suma `"reprogramar"` + `"actualizar datos de clientes"`.
