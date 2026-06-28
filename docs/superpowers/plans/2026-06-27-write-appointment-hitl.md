# Write-tool `create_appointment` con HITL — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar `action_stub` por la primera tool de escritura real (`create_appointment`) detrás de un `interrupt` de LangGraph con tarjeta de confirmación (human-in-the-loop).

**Architecture:** El router rutea `intent="action"` a **dos nodos**: `propose_appointment` (LLM extrae args tipados → resolver determinístico nombre→UUID/fecha→ISO; se **checkpointea**) y `confirm_appointment` (`interrupt(proposed_action)` → al reanudar, `db.create_appointment(...)` o cancela). Separar en dos nodos evita que el LLM se re-ejecute al reanudar (el `interrupt` re-corre el nodo desde el principio), garantizando que se escribe exactamente lo confirmado. La tarjeta llega al front por un evento SSE `confirm`; el front reanuda vía `POST /chat/resume` con el `thread_id`.

**Tech Stack:** Python 3.11+/FastAPI/LangGraph 0.2.x (`interrupt`/`Command`, checkpointer Postgres ya cableado) · asyncpg · Pydantic · Ollama `gemma4:12b` (solo en la extracción) · Next.js 15/React 19 + assistant-ui `useLocalRuntime` · vitest+jsdom+@testing-library/react.

Spec: `docs/superpowers/specs/2026-06-27-write-appointment-hitl-design.md`. Contrato: `CLAUDE.md`.

## Global Constraints

- **Local-first · $0**: inferencia 100% local vía Ollama (`http://localhost:11434`); prohibido llamar APIs externas. Sin deps nuevas (back ni front).
- **Escrituras solo por tool parametrizada + confirmación humana** (CLAUDE.md §4): nunca SQL libre; el LLM **propone**, el humano confirma, recién ahí se escribe.
- **Aislamiento multi-tenant por `practice_id` siempre** (CLAUDE.md §0.5): resolución e INSERT scopeados; guarda `EXISTS(... AND practice_id=$)` en el write.
- **Commits LIMPIOS** (CLAUDE.md §6): el autor es el usuario; **prohibida** cualquier atribución/trailer a Claude/Anthropic.
- **Rama de trabajo**: `slice-4-write-appointment-hitl` (ya creada; el spec ya está commiteado ahí).
- **mypy SIEMPRE** con `--config-file backend/pyproject.toml` (sin eso da falso-positivo `asyncpg [import-untyped]`).
- **Gate no-llm**: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` debe quedar verde. Las pruebas con DB van marcadas `@pytest.mark.integration` (las corre el gate no-llm; requieren Postgres `docker compose up -d`).
- **PowerShell**: NO soporta `cd x && y`; usar `;` o rutas absolutas. No pushear (convención local-first).

## File Structure

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backend/app/config.py` | Modify | +`appt_default_duration_min`, +`appt_name_match_limit` |
| `backend/app/graph/state.py` | Modify | +campo `proposed_action: dict \| None` |
| `backend/app/db.py` | Modify | +`find_clients_by_name`, +`find_practitioners_by_name`, +`list_active_practitioners`, +`create_appointment` |
| `backend/app/agents/action_agent.py` | Create | `propose_appointment`: extracción LLM (args tipados) + resolver determinístico |
| `backend/app/graph/nodes.py` | Modify | +`propose_appointment_node`, +`confirm_appointment_node`, +`_format_receipt`; −`action_stub`/`STUB_MESSAGE` |
| `backend/app/graph/edges.py` | Modify | `action`→`propose_appointment`; +`route_after_propose` |
| `backend/app/graph/build.py` | Modify | registra los 2 nodos, quita `action_stub`, cablea el conditional |
| `backend/app/main.py` | Modify | `/chat` multi-mode + evento SSE `confirm`; +`POST /chat/resume` |
| `backend/tests/test_create_appointment.py` | Create | integración del INSERT + guarda de tenant |
| `backend/tests/test_action_agent.py` | Create | resolver (no-llm, fakes) |
| `backend/tests/test_hitl_cycle.py` | Create | interrupt→resume con `MemorySaver` (no-llm) |
| `backend/tests/test_nodes.py` | Modify | −test del stub; +propose node |
| `backend/tests/test_edges.py` | Create | `route_after_propose` + mapeo de intent |
| `backend/tests/test_sse_stream.py` | Create | helper SSE traduce token/sources/confirm/done (no-llm) |
| `backend/tests/test_action_e2e_llm.py` | Create | e2e `-m llm` confirm/cancel |
| `frontend/lib/chatStream.ts` | Modify | +evento `confirm`, +`resumeChat`, refactor `streamSSE` |
| `frontend/lib/chatStream.test.ts` | Modify | +parse `confirm`, +`resumeChat` |
| `frontend/lib/runtime.ts` | Modify | `useChatRuntime(onConfirm?)` surface del `confirm` |
| `frontend/components/ConfirmCard.tsx` | Create | tarjeta Confirmar/Cancelar + recibo |
| `frontend/components/ConfirmCard.test.tsx` | Create | render + click + recibo |
| `frontend/app/page.tsx` | Modify | estado `pending` + render `ConfirmCard` |
| `frontend/SMOKE.md` | Modify | paso de escritura → tarjeta → confirmar |

---

### Task 1: Fundación — `proposed_action` en el state + knobs de config

**Files:**
- Modify: `backend/app/graph/state.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_state.py` (Create)

**Interfaces:**
- Produces: `AgentState["proposed_action"]: dict | None`; `new_state(...)` lo inicializa en `None`. `Settings.appt_default_duration_min: int = 30`, `Settings.appt_name_match_limit: int = 5`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_state.py`:

```python
from app.config import get_settings
from app.graph.state import new_state


def test_new_state_inits_proposed_action_none() -> None:
    state = new_state("hola", "pid", "tid")
    assert state["proposed_action"] is None


def test_appointment_config_defaults() -> None:
    s = get_settings()
    assert s.appt_default_duration_min == 30
    assert s.appt_name_match_limit == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_state.py -q`
Expected: FAIL (`KeyError: 'proposed_action'` / `AttributeError: ... appt_default_duration_min`).

- [ ] **Step 3: Add the config knobs**

In `backend/app/config.py`, after the line `sql_max_attempts: int = 2`:

```python
    sql_max_attempts: int = 2
    appt_default_duration_min: int = 30
    appt_name_match_limit: int = 5
```

- [ ] **Step 4: Add the state field**

In `backend/app/graph/state.py`, add to the `AgentState` TypedDict (after `judge_scores: dict`):

```python
    judge_scores: dict
    proposed_action: dict | None
```

And in `new_state`, add the key to the returned dict (after `"judge_scores": {},`):

```python
        "judge_scores": {},
        "proposed_action": None,
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_state.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/graph/state.py backend/tests/test_state.py
git commit -m "feat(graph): AgentState.proposed_action + config de turnos (HITL write slice)"
```

---

### Task 2: Tool parametrizada y resolvers en `db.py`

**Files:**
- Modify: `backend/app/db.py`
- Test: `backend/tests/test_create_appointment.py` (Create)

**Interfaces:**
- Consumes: `db.get_pool()` (existente).
- Produces:
  - `find_clients_by_name(practice_id: str, name: str, *, limit: int) -> list[dict[str, Any]]` (claves `id`, `full_name`)
  - `find_practitioners_by_name(practice_id: str, name: str, *, limit: int) -> list[dict[str, Any]]`
  - `list_active_practitioners(practice_id: str) -> list[dict[str, Any]]`
  - `create_appointment(practice_id, client_id, practitioner_id, start_at: datetime, end_at: datetime, *, reason=None, channel=None, status="programado", created_by=None) -> dict[str, Any]` (claves `id`, `start_at`, `end_at`, `status`); levanta `RuntimeError` si client/practitioner no son de la práctica.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_create_appointment.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import db
from app.config import get_settings


@pytest.mark.integration
async def test_create_appointment_inserts_and_returns_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = await db.find_clients_by_name(pid, "", limit=1)
    pracs = await db.list_active_practitioners(pid)
    assert client and pracs  # el seed cargó datos
    start = datetime.now(UTC) + timedelta(days=1)
    row = await db.create_appointment(
        pid, client[0]["id"], pracs[0]["id"], start, start + timedelta(minutes=30),
        reason="control", channel="presencial",
    )
    assert row["status"] == "programado"
    assert row["id"]


@pytest.mark.integration
async def test_create_appointment_rejects_foreign_ids() -> None:
    pid = get_settings().practice_id
    with pytest.raises(RuntimeError):
        await db.create_appointment(
            pid, str(uuid4()), str(uuid4()),
            datetime.now(UTC), datetime.now(UTC) + timedelta(minutes=30),
        )


@pytest.mark.integration
async def test_find_clients_by_name_is_tenant_scoped() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    rows = await db.find_clients_by_name(pid, "", limit=3)
    assert all("id" in r and "full_name" in r for r in rows)
```

> `find_clients_by_name(..., "")` con patrón vacío hace `ILIKE '%%'` → trae cualquiera (sirve para tomar un id del seed). En el agente nunca se llama con `""` (el LLM siempre da un nombre).

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_create_appointment.py -q`
Expected: FAIL (`AttributeError: module 'app.db' has no attribute 'find_clients_by_name'`).

- [ ] **Step 3: Implement the db functions**

In `backend/app/db.py`, add `from datetime import datetime` at the top imports (junto a los existentes) and append these functions at the end of the file:

```python
async def find_clients_by_name(
    practice_id: str, name: str, *, limit: int
) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, full_name FROM clients
        WHERE practice_id = $1 AND full_name ILIKE '%' || $2 || '%'
        ORDER BY full_name LIMIT $3
        """,
        practice_id,
        name,
        limit,
    )
    return [dict(r) for r in rows]


async def find_practitioners_by_name(
    practice_id: str, name: str, *, limit: int
) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, full_name FROM practitioners
        WHERE practice_id = $1 AND active AND full_name ILIKE '%' || $2 || '%'
        ORDER BY full_name LIMIT $3
        """,
        practice_id,
        name,
        limit,
    )
    return [dict(r) for r in rows]


async def list_active_practitioners(practice_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id::text, full_name FROM practitioners "
        "WHERE practice_id = $1 AND active ORDER BY full_name",
        practice_id,
    )
    return [dict(r) for r in rows]


async def create_appointment(
    practice_id: str,
    client_id: str,
    practitioner_id: str,
    start_at: datetime,
    end_at: datetime,
    *,
    reason: str | None = None,
    channel: str | None = None,
    status: str = "programado",
    created_by: str | None = None,
) -> dict[str, Any]:
    """Tool de escritura parametrizada. Verifica que client y practitioner sean
    de la práctica (defensa en profundidad sobre el resolver) y recién inserta."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ok_client = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM clients WHERE id = $1 AND practice_id = $2)",
                client_id,
                practice_id,
            )
            ok_prac = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM practitioners WHERE id = $1 AND practice_id = $2)",
                practitioner_id,
                practice_id,
            )
            if not (ok_client and ok_prac):
                raise RuntimeError(
                    "create_appointment: cliente/profesional fuera de la práctica o inexistente"
                )
            row = await conn.fetchrow(
                """
                INSERT INTO appointments
                    (practice_id, client_id, practitioner_id, start_at, end_at,
                     status, reason, channel, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id::text, start_at, end_at, status
                """,
                practice_id,
                client_id,
                practitioner_id,
                start_at,
                end_at,
                status,
                reason,
                channel,
                created_by,
            )
    if row is None:
        raise RuntimeError("create_appointment: la inserción no devolvió fila")
    return dict(row)
```

- [ ] **Step 4: Run test to verify it passes**

Ensure Postgres is up (`docker compose up -d`).
Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_create_appointment.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_create_appointment.py
git commit -m "feat(db): tool parametrizada create_appointment + resolvers scoped por practice_id"
```

---

### Task 3: `action_agent.propose_appointment` — extracción + resolver

**Files:**
- Create: `backend/app/agents/action_agent.py`
- Test: `backend/tests/test_action_agent.py` (Create)

**Interfaces:**
- Consumes: `db.find_clients_by_name`, `db.find_practitioners_by_name`, `db.list_active_practitioners` (Task 2); `get_settings().appt_*` (Task 1); `make_llm`.
- Produces:
  - `ProposedAppointment(BaseModel)`: `client_name: str`, `practitioner_name: str | None = None`, `start_at: str`, `duration_min: int = 30`, `reason: str | None = None`, `channel: Literal["presencial","telellamada"] | None = None`.
  - `ProposalResult` (dataclass): `proposed_action: dict | None`, `abstained: bool`, `message: str`, `reason: str`.
  - `propose_appointment(question: str, practice_id: str, *, now: datetime, gen_llm: Any = None) -> ProposalResult`. El `proposed_action` tiene `{"kind","summary","params"}` con `params` JSON-serializable (`start_at`/`end_at` como ISO str).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_action_agent.py`:

```python
from datetime import UTC, datetime

from app import db
from app.agents import action_agent
from app.agents.action_agent import ProposalResult, ProposedAppointment

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedAppointment) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedAppointment) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _patch_db(monkeypatch, *, clients, pracs_by_name=None, active_pracs=None):  # type: ignore[no-untyped-def]
    async def _clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    async def _pracs_by_name(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return pracs_by_name or []

    async def _active(practice_id):  # type: ignore[no-untyped-def]
        return active_pracs or []

    monkeypatch.setattr(db, "find_clients_by_name", _clients)
    monkeypatch.setattr(db, "find_practitioners_by_name", _pracs_by_name)
    monkeypatch.setattr(db, "list_active_practitioners", _active)


async def test_happy_path_defaults_single_practitioner(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Ana mañana 10", "pid", now=NOW, gen_llm=llm)
    assert not result.abstained
    assert result.proposed_action is not None
    params = result.proposed_action["params"]
    assert params["client_id"] == "c1"
    assert params["practitioner_id"] == "p1"
    assert params["start_at"] == "2026-06-30T10:00:00+00:00"
    assert params["end_at"] == "2026-06-30T10:30:00+00:00"
    assert "Ana López" in result.proposed_action["summary"]


async def test_abstains_when_client_not_found(monkeypatch) -> None:
    _patch_db(monkeypatch, clients=[], active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}])
    llm = FakeGenLLM(ProposedAppointment(client_name="Zzz", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Zzz", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "client_not_found"
    assert "Zzz" in result.message


async def test_abstains_when_client_ambiguous(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}, {"id": "c2", "full_name": "Ana Pérez"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "client_ambiguous"


async def test_abstains_when_practitioner_unspecified_and_many(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}, {"id": "p2", "full_name": "Dr. Ruiz"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "practitioner_unspecified"


async def test_abstains_on_bad_datetime(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="no es fecha"))
    result = await action_agent.propose_appointment("agendá a Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "datetime_parse_failed"


def test_proposal_result_is_a_dataclass() -> None:
    r = ProposalResult(proposed_action=None, abstained=True, message="m", reason="r")
    assert r.abstained and r.message == "m"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_action_agent.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.agents.action_agent'`).

- [ ] **Step 3: Implement the agent**

Create `backend/app/agents/action_agent.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from app import db
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude armar el turno con esos datos. ¿Probás de nuevo indicando cliente, "
    "profesional y horario?"
)


class ProposedAppointment(BaseModel):
    client_name: str
    practitioner_name: str | None = None
    start_at: str
    duration_min: int = 30
    reason: str | None = None
    channel: Literal["presencial", "telellamada"] | None = None


@dataclass
class ProposalResult:
    proposed_action: dict[str, Any] | None
    abstained: bool
    message: str
    reason: str


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt(now: datetime, default_duration: int) -> str:
    return (
        "Sos el asistente de agenda de una práctica profesional. A partir del pedido del "
        "usuario, extraé los datos para crear UN turno. La fecha y hora actuales son "
        f"{now.isoformat()} (UTC). Devolvé start_at como fecha/hora ABSOLUTA en ISO 8601 "
        "(resolvé expresiones como 'mañana' o 'el martes' contra la fecha actual). Si no se "
        f"menciona la duración, usá {default_duration} minutos. client_name es la persona del "
        "turno; practitioner_name SOLO si se menciona un profesional; channel SOLO si se aclara "
        "('presencial' o 'telellamada')."
    )


async def _extract(question: str, now: datetime, gen_llm: Any) -> ProposedAppointment | None:
    settings = get_settings()
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedAppointment)
    try:
        result = await structured.ainvoke(
            [
                ("system", _system_prompt(now, settings.appt_default_duration_min)),
                ("human", question),
            ]
        )
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedAppointment) else None


def _abstain(message: str, reason: str) -> ProposalResult:
    return ProposalResult(proposed_action=None, abstained=True, message=message, reason=reason)


def _summary(params: dict[str, Any], start: datetime, end: datetime) -> str:
    when = f"{start.strftime('%d/%m %H:%M')}–{end.strftime('%H:%M')}"
    parts = [f"Crear turno: {params['client_name']} con {params['practitioner_name']} — {when}"]
    if params["reason"]:
        parts.append(f"motivo: {params['reason']}")
    if params["channel"]:
        parts.append(params["channel"])
    return ", ".join(parts)


async def propose_appointment(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    clients = await db.find_clients_by_name(
        practice_id, extracted.client_name, limit=settings.appt_name_match_limit
    )
    if not clients:
        return _abstain(
            f"No encontré ningún cliente que coincida con «{extracted.client_name}». "
            "¿Me das el nombre completo?",
            "client_not_found",
        )
    if len(clients) > 1:
        names = ", ".join(c["full_name"] for c in clients)
        return _abstain(
            f"Hay varios clientes que coinciden con «{extracted.client_name}»: {names}. ¿Cuál es?",
            "client_ambiguous",
        )
    client = clients[0]

    if extracted.practitioner_name:
        pracs = await db.find_practitioners_by_name(
            practice_id, extracted.practitioner_name, limit=settings.appt_name_match_limit
        )
        if not pracs:
            return _abstain(
                f"No encontré ningún profesional que coincida con «{extracted.practitioner_name}».",
                "practitioner_not_found",
            )
        if len(pracs) > 1:
            names = ", ".join(p["full_name"] for p in pracs)
            return _abstain(
                f"Hay varios profesionales que coinciden con «{extracted.practitioner_name}»: "
                f"{names}. ¿Cuál?",
                "practitioner_ambiguous",
            )
        prac = pracs[0]
    else:
        pracs = await db.list_active_practitioners(practice_id)
        if not pracs:
            return _abstain("No hay profesionales activos cargados en la práctica.", "no_practitioners")
        if len(pracs) > 1:
            names = ", ".join(p["full_name"] for p in pracs)
            return _abstain(f"¿Con qué profesional? Tenés: {names}.", "practitioner_unspecified")
        prac = pracs[0]

    try:
        start = datetime.fromisoformat(extracted.start_at)
    except ValueError:
        return _abstain(
            "No entendí la fecha/hora del turno. ¿Me la indicás? (p. ej. 'mañana a las 10:00').",
            "datetime_parse_failed",
        )
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    duration = extracted.duration_min if extracted.duration_min > 0 else settings.appt_default_duration_min
    end = start + timedelta(minutes=duration)

    params: dict[str, Any] = {
        "client_id": client["id"],
        "client_name": client["full_name"],
        "practitioner_id": prac["id"],
        "practitioner_name": prac["full_name"],
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "reason": extracted.reason,
        "channel": extracted.channel,
        "status": "programado",
    }
    return ProposalResult(
        proposed_action={"kind": "create_appointment", "summary": _summary(params, start, end), "params": params},
        abstained=False,
        message="",
        reason="ok",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_action_agent.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/action_agent.py backend/tests/test_action_agent.py
git commit -m "feat(agents): action_agent.propose_appointment (extracción + resolver determinístico)"
```

---

### Task 4: Grafo — nodos `propose`/`confirm` + `interrupt`, reemplaza `action_stub`

**Files:**
- Modify: `backend/app/graph/nodes.py`
- Modify: `backend/app/graph/edges.py`
- Modify: `backend/app/graph/build.py`
- Test: `backend/tests/test_edges.py` (Create), `backend/tests/test_hitl_cycle.py` (Create), `backend/tests/test_nodes.py` (Modify)

**Interfaces:**
- Consumes: `propose_appointment` (Task 3), `db.create_appointment` (Task 2), `AgentState.proposed_action` (Task 1), `interrupt` (langgraph).
- Produces:
  - `nodes.propose_appointment_node(state) -> dict` (devuelve `{"proposed_action": ...}`; en abstención emite el mensaje y devuelve `proposed_action=None`).
  - `nodes.confirm_appointment_node(state) -> dict` (`interrupt(action)`; confirm→`create_appointment`+recibo, cancel→mensaje).
  - `nodes._format_receipt(params: dict, row: dict) -> str`.
  - `edges.route_after_propose(state) -> str` (`"confirm_appointment"` o `END`); `edges._INTENT_TO_NODE["action"] == "propose_appointment"`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_edges.py`:

```python
from langgraph.graph import END

from app.graph.edges import _INTENT_TO_NODE, route_after_propose
from app.graph.state import new_state


def test_action_intent_routes_to_propose() -> None:
    assert _INTENT_TO_NODE["action"] == "propose_appointment"


def test_route_after_propose_to_confirm_when_action_present() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = {"kind": "create_appointment"}
    assert route_after_propose(state) == "confirm_appointment"


def test_route_after_propose_to_end_when_abstained() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = None
    assert route_after_propose(state) == END
```

Create `backend/tests/test_hitl_cycle.py`:

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.action_agent import ProposalResult
from app.graph import edges, nodes
from app.graph.state import AgentState, new_state

ACTION = {
    "kind": "create_appointment",
    "summary": "Crear turno: Ana López con Dra. Gómez — 30/06 10:00–10:30",
    "params": {
        "client_id": "c1",
        "client_name": "Ana López",
        "practitioner_id": "p1",
        "practitioner_name": "Dra. Gómez",
        "start_at": "2026-06-30T10:00:00+00:00",
        "end_at": "2026-06-30T10:30:00+00:00",
        "reason": "control",
        "channel": "presencial",
        "status": "programado",
    },
}


class _Spy:
    def __init__(self, ret):  # type: ignore[no-untyped-def]
        self.ret = ret
        self.calls: list = []

    async def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((args, kwargs))
        return self.ret


def _hitl_graph():  # type: ignore[no-untyped-def]
    g = StateGraph(AgentState)
    g.add_node("propose_appointment", nodes.propose_appointment_node)
    g.add_node("confirm_appointment", nodes.confirm_appointment_node)
    g.add_edge(START, "propose_appointment")
    g.add_conditional_edges(
        "propose_appointment",
        edges.route_after_propose,
        {"confirm_appointment": "confirm_appointment", END: END},
    )
    g.add_edge("confirm_appointment", END)
    return g.compile(checkpointer=MemorySaver())


async def _fake_propose(question, practice_id, *, now, gen_llm=None):  # type: ignore[no-untyped-def]
    return ProposalResult(proposed_action=ACTION, abstained=False, message="", reason="ok")


async def test_confirm_writes_appointment_exactly_once(monkeypatch) -> None:
    spy = _Spy({"id": "appt-1", "status": "programado"})
    monkeypatch.setattr(nodes, "propose_appointment", _fake_propose)
    monkeypatch.setattr(nodes, "create_appointment", spy)
    graph = _hitl_graph()
    config = {"configurable": {"thread_id": "t-confirm"}}

    await graph.ainvoke(new_state("agendá a Ana mañana 10", "pid", "t-confirm"), config)
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_appointment",)
    assert snap.tasks[0].interrupts[0].value["kind"] == "create_appointment"
    assert spy.calls == []  # todavía no se escribió

    await graph.ainvoke(Command(resume="confirm"), config)
    assert len(spy.calls) == 1  # se escribió UNA vez (sin recomputar la propuesta)


async def test_cancel_writes_nothing(monkeypatch) -> None:
    spy = _Spy({"id": "appt-1", "status": "programado"})
    monkeypatch.setattr(nodes, "propose_appointment", _fake_propose)
    monkeypatch.setattr(nodes, "create_appointment", spy)
    graph = _hitl_graph()
    config = {"configurable": {"thread_id": "t-cancel"}}

    await graph.ainvoke(new_state("agendá a Ana mañana 10", "pid", "t-cancel"), config)
    await graph.ainvoke(Command(resume="cancel"), config)
    assert spy.calls == []
```

In `backend/tests/test_nodes.py`: **delete** `test_action_stub_streams_not_available` (lines that reference `nodes.action_stub`/`nodes.STUB_MESSAGE`) and **add** a propose-node abstention test:

```python
async def test_propose_node_abstains_emits_message(monkeypatch):
    from app.agents.action_agent import ProposalResult

    async def _fake_propose(question, practice_id, *, now, gen_llm=None):
        return ProposalResult(proposed_action=None, abstained=True, message="No encontré al cliente.", reason="client_not_found")

    monkeypatch.setattr(nodes, "propose_appointment", _fake_propose)
    tokens, sources = await _run(nodes.propose_appointment_node, new_state("agendá", "p", "t"))
    assert tokens == "No encontré al cliente."
    assert sources == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_edges.py backend/tests/test_hitl_cycle.py -q`
Expected: FAIL (`ImportError: cannot import name 'route_after_propose'` / `AttributeError: ... propose_appointment_node`).

- [ ] **Step 3: Update `edges.py`**

Replace the full contents of `backend/app/graph/edges.py`:

```python
from langgraph.graph import END

from app.graph.state import AgentState

_INTENT_TO_NODE = {
    "rag": "rag",
    "sql": "sql_node",
    "action": "propose_appointment",
    "chitchat": "chitchat",
    "out_of_scope": "scope_reject",
}


def route(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state["intent"], "scope_reject")


def route_after_propose(state: AgentState) -> str:
    return "confirm_appointment" if state.get("proposed_action") else END
```

- [ ] **Step 4: Update `nodes.py`**

In `backend/app/graph/nodes.py`: add imports at the top (after the existing imports):

```python
from datetime import UTC, datetime

from langgraph.types import interrupt

from app.agents.action_agent import propose_appointment
from app.db import create_appointment
```

Delete the `STUB_MESSAGE = ...` constant and the entire `async def action_stub(...)` function. Append the new nodes at the end of the file:

```python
def _format_receipt(params: dict, row: dict) -> str:
    start = datetime.fromisoformat(params["start_at"])
    return (
        f"✅ Turno creado: {params['client_name']} con {params['practitioner_name']} "
        f"el {start.strftime('%d/%m %H:%M')} (estado: {row['status']})."
    )


async def propose_appointment_node(state: AgentState) -> dict:
    result = await propose_appointment(
        last_user_text(state), state["practice_id"], now=datetime.now(UTC)
    )
    if result.abstained:
        write_token(result.message)
        write_sources([])
        return {
            "proposed_action": None,
            "sources": [],
            "messages": [AIMessage(content=result.message)],
        }
    return {"proposed_action": result.proposed_action}


async def confirm_appointment_node(state: AgentState) -> dict:
    action = state["proposed_action"] or {}
    decision = interrupt(action)
    if decision == "confirm":
        params = action["params"]
        row = await create_appointment(
            state["practice_id"],
            params["client_id"],
            params["practitioner_id"],
            datetime.fromisoformat(params["start_at"]),
            datetime.fromisoformat(params["end_at"]),
            reason=params.get("reason"),
            channel=params.get("channel"),
            status=params.get("status", "programado"),
        )
        msg = _format_receipt(params, row)
    else:
        msg = "Cancelado, no creé el turno."
    write_token(msg)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=msg)]}
```

- [ ] **Step 5: Update `build.py`**

Replace the full contents of `backend/app/graph/build.py`:

```python
from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.edges import route, route_after_propose
from app.graph.nodes import (
    chitchat_node,
    confirm_appointment_node,
    propose_appointment_node,
    rag_node,
    scope_reject_node,
    sql_node,
)
from app.graph.router import router_node
from app.graph.state import AgentState

_LEAF_NODES = ("rag", "chitchat", "scope_reject", "sql_node", "confirm_appointment")


def build_graph(checkpointer: Any = None) -> Any:
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("rag", rag_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("scope_reject", scope_reject_node)
    g.add_node("sql_node", sql_node)
    g.add_node("propose_appointment", propose_appointment_node)
    g.add_node("confirm_appointment", confirm_appointment_node)

    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router",
        route,
        {
            "rag": "rag",
            "chitchat": "chitchat",
            "scope_reject": "scope_reject",
            "sql_node": "sql_node",
            "propose_appointment": "propose_appointment",
        },
    )
    g.add_conditional_edges(
        "propose_appointment",
        route_after_propose,
        {"confirm_appointment": "confirm_appointment", END: END},
    )
    for node in _LEAF_NODES:
        g.add_edge(node, END)

    return g.compile(checkpointer=checkpointer)


@lru_cache
def get_default_graph() -> Any:
    """Grafo sin checkpointer (tests / fallback cuando el lifespan no corrió).
    Nota: el camino de escritura (interrupt) requiere checkpointer; en runtime
    real lo provee el lifespan (AsyncPostgresSaver)."""
    return build_graph(checkpointer=None)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_edges.py backend/tests/test_hitl_cycle.py backend/tests/test_nodes.py -q`
Expected: PASS (all green; `test_nodes` no longer references the stub).

- [ ] **Step 7: Run the no-llm gate (regression)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (todo verde; sin referencias colgadas a `action_stub`).

- [ ] **Step 8: Lint + types**

Run: `backend\.venv\Scripts\ruff check backend` then `backend\.venv\Scripts\ruff format backend` then `backend\.venv\Scripts\python -m mypy app --config-file backend/pyproject.toml` (desde `backend/`, o `mypy backend/app --config-file backend/pyproject.toml` desde la raíz).
Expected: sin errores.

- [ ] **Step 9: Commit**

```bash
git add backend/app/graph/nodes.py backend/app/graph/edges.py backend/app/graph/build.py backend/tests/test_edges.py backend/tests/test_hitl_cycle.py backend/tests/test_nodes.py
git commit -m "feat(graph): write-path HITL — nodos propose/confirm + interrupt, reemplaza action_stub"
```

---

### Task 5: Transporte — `/chat` multi-mode + evento `confirm` + `/chat/resume`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_sse_stream.py` (Create)

**Interfaces:**
- Consumes: `build_graph`/`get_default_graph`, `Command` (langgraph), `new_state`.
- Produces: `main._sse_event_stream(graph, inp, config)` (async generator de dicts SSE: `token`/`sources`/`confirm`/`done`); `POST /chat/resume` con body `{thread_id, decision}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sse_stream.py`:

```python
import json
from types import SimpleNamespace

from app.main import _sse_event_stream


class _FakeGraph:
    def __init__(self, items):  # type: ignore[no-untyped-def]
        self._items = items

    def astream(self, inp, config, *, stream_mode):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            for it in self._items:
                yield it

        return gen()


async def test_stream_translates_token_sources_confirm_done() -> None:
    action = {"kind": "create_appointment", "summary": "Crear turno: Ana", "params": {}}
    graph = _FakeGraph(
        [
            ("custom", {"kind": "token", "text": "hola"}),
            ("custom", {"kind": "sources", "sources": []}),
            ("updates", {"propose_appointment": {"proposed_action": action}}),  # ignorado
            ("updates", {"__interrupt__": (SimpleNamespace(value=action),)}),
        ]
    )
    config = {"configurable": {"thread_id": "t1"}}
    events = [e async for e in _sse_event_stream(graph, None, config)]

    assert {"event": "token", "data": "hola"} in events
    assert {"event": "sources", "data": "[]"} in events
    confirm = next(e for e in events if e["event"] == "confirm")
    payload = json.loads(confirm["data"])
    assert payload["thread_id"] == "t1"
    assert payload["action"] == action
    assert events[-1] == {"event": "done", "data": "[DONE]"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sse_stream.py -q`
Expected: FAIL (`ImportError: cannot import name '_sse_event_stream'`).

- [ ] **Step 3: Rewrite the chat transport in `main.py`**

In `backend/app/main.py`: add `Literal` to the typing import and add the `Command` import near the other langgraph imports:

```python
from typing import Any, Literal
```
```python
from langgraph.types import Command
```

Replace the current `@app.post("/chat")` block (from `class ChatRequest` through the end of the `chat` function) with:

```python
class ChatRequest(BaseModel):
    message: str


class ResumeRequest(BaseModel):
    thread_id: str
    decision: Literal["confirm", "cancel"]


async def _sse_event_stream(graph: Any, inp: Any, config: dict) -> AsyncIterator[dict]:
    tid = config["configurable"]["thread_id"]
    async for mode, chunk in graph.astream(inp, config, stream_mode=["custom", "updates"]):
        if mode == "custom":
            kind = chunk.get("kind")
            if kind == "token":
                yield {"event": "token", "data": chunk["text"]}
            elif kind == "sources":
                yield {"event": "sources", "data": json.dumps(chunk["sources"], ensure_ascii=False)}
        elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupts = chunk["__interrupt__"]
            value = interrupts[0].value if interrupts else {}
            yield {
                "event": "confirm",
                "data": json.dumps({"thread_id": tid, "action": value}, ensure_ascii=False),
            }
    yield {"event": "done", "data": "[DONE]"}


@app.post("/chat")
async def chat(req: ChatRequest, request: Request) -> EventSourceResponse:
    # El router e4b (y los nodos LLM) necesitan Ollama: si está caído, 503 amable
    # antes de abrir el stream SSE (preserva el fix de la limpieza pre-Fase 1).
    if not await ollama_available():
        raise HTTPException(
            status_code=503,
            detail="El asistente local (Ollama) no está disponible. "
            "Verificá que Ollama esté corriendo y volvé a intentar.",
        )

    graph = getattr(request.app.state, "graph", None) or get_default_graph()
    s = get_settings()
    state = new_state(req.message, practice_id=s.practice_id, thread_id=str(uuid4()))
    config = {"configurable": {"thread_id": state["thread_id"]}}
    return EventSourceResponse(_sse_event_stream(graph, state, config))


@app.post("/chat/resume")
async def chat_resume(req: ResumeRequest, request: Request) -> EventSourceResponse:
    # El resume es determinístico (recibo/cancelación sin LLM) → no probamos Ollama.
    graph = getattr(request.app.state, "graph", None) or get_default_graph()
    config = {"configurable": {"thread_id": req.thread_id}}
    return EventSourceResponse(_sse_event_stream(graph, Command(resume=req.decision), config))
```

> The old inline `event_stream()` is removed (its logic now lives in `_sse_event_stream`, shared by `/chat` and `/chat/resume`).

- [ ] **Step 4: Run test to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sse_stream.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Lint + types + no-llm gate**

Run: `backend\.venv\Scripts\ruff check backend` ; `backend\.venv\Scripts\python -m mypy app --config-file backend/pyproject.toml` (desde `backend/`) ; `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: verde.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_sse_stream.py
git commit -m "feat(api): surface del interrupt por SSE + endpoint /chat/resume"
```

---

### Task 6: Frontend transport — evento `confirm` + `resumeChat`

**Files:**
- Modify: `frontend/lib/chatStream.ts`
- Test: `frontend/lib/chatStream.test.ts` (Modify)

**Interfaces:**
- Produces: `ProposedAction` interface (`{kind, summary, params}`); `ChatEvent` suma `{type:"confirm"; threadId; action}`; `streamChat(message, signal?)` (sin cambio de comportamiento); `resumeChat(threadId, decision, signal?)`.

- [ ] **Step 1: Write the failing tests**

In `frontend/lib/chatStream.test.ts`: change the import line to include `resumeChat`:

```ts
import { resumeChat, streamChat } from "./chatStream";
```

Append two tests:

```ts
test("streamChat yields a confirm event with threadId and action", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
    'event: confirm\ndata: {"thread_id":"t1","action":{"kind":"create_appointment","summary":"Crear turno: Ana","params":{}}}\n\n',
    "event: done\ndata: [DONE]\n\n",
  ])));

  const events = [];
  for await (const ev of streamChat("agendá")) events.push(ev);

  expect(events).toEqual([
    { type: "confirm", threadId: "t1", action: { kind: "create_appointment", summary: "Crear turno: Ana", params: {} } },
    { type: "done" },
  ]);
});

test("resumeChat posts thread_id and decision to /api/chat/resume", async () => {
  const fetchMock = vi.fn().mockResolvedValue(sseResponse([
    "event: token\ndata: ✅\n\nevent: done\ndata: [DONE]\n\n",
  ]));
  vi.stubGlobal("fetch", fetchMock);

  const events = [];
  for await (const ev of resumeChat("t1", "confirm")) events.push(ev);

  expect(fetchMock).toHaveBeenCalledWith("/api/chat/resume", expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ thread_id: "t1", decision: "confirm" }),
  }));
  expect(events).toEqual([{ type: "token", text: "✅" }, { type: "done" }]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (PowerShell): `cd frontend; npx vitest run lib/chatStream.test.ts`
Expected: FAIL (`resumeChat` is not exported; `confirm` event not parsed).

- [ ] **Step 3: Rewrite `chatStream.ts`**

Replace the full contents of `frontend/lib/chatStream.ts`:

```ts
export interface Source {
  n: number;
  title: string;
  page: number | null;
  document_id: string;
}

export interface ProposedAction {
  kind: string;
  summary: string;
  params: Record<string, unknown>;
}

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "sources"; sources: Source[] }
  | { type: "confirm"; threadId: string; action: ProposedAction }
  | { type: "done" };

function parseEvent(raw: string): ChatEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  const data = dataLines.join("\n");
  if (event === "token") return { type: "token", text: data };
  if (event === "sources") return { type: "sources", sources: JSON.parse(data) as Source[] };
  if (event === "confirm") {
    const parsed = JSON.parse(data) as { thread_id: string; action: ProposedAction };
    return { type: "confirm", threadId: parsed.thread_id, action: parsed.action };
  }
  if (event === "done") return { type: "done" };
  return null;
}

async function* streamSSE(
  url: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = `chat failed: ${res.status}`;
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed?.detail) detail = parsed.detail;
    } catch {
      // cuerpo no-JSON: dejamos el mensaje por defecto
    }
    throw new Error(detail);
  }
  if (!res.body) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // sse_starlette delimits events with \r\n\r\n; tolerate \n\n too.
    let m: RegExpExecArray | null;
    const sep = /\r?\n\r?\n/;
    while ((m = sep.exec(buffer)) !== null) {
      const raw = buffer.slice(0, m.index);
      buffer = buffer.slice(m.index + m[0].length);
      const ev = parseEvent(raw);
      if (ev) yield ev;
    }
  }
}

export function streamChat(message: string, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  return streamSSE("/api/chat", { message }, signal);
}

export function resumeChat(
  threadId: string,
  decision: "confirm" | "cancel",
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  return streamSSE("/api/chat/resume", { thread_id: threadId, decision }, signal);
}
```

- [ ] **Step 4: Run the full frontend suite to verify it passes**

Run (PowerShell): `cd frontend; npx vitest run`
Expected: PASS (los tests previos de `streamChat` siguen verdes + los 2 nuevos).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/chatStream.ts frontend/lib/chatStream.test.ts
git commit -m "feat(front): evento confirm + resumeChat en chatStream"
```

---

### Task 7: Frontend UI — `ConfirmCard` + wiring en runtime/page

**Files:**
- Create: `frontend/components/ConfirmCard.tsx`
- Test: `frontend/components/ConfirmCard.test.tsx` (Create)
- Modify: `frontend/lib/runtime.ts`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: `resumeChat`, `ProposedAction` (Task 6).
- Produces: `ConfirmCard({threadId, action, onClose})`; `PendingAction` (`{threadId, action}`) exportado por `runtime.ts`; `useChatRuntime(onConfirm?)`.

- [ ] **Step 1: Write the failing test**

Create `frontend/components/ConfirmCard.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as chatStream from "../lib/chatStream";
import { ConfirmCard } from "./ConfirmCard";

afterEach(() => vi.restoreAllMocks());

const action = { kind: "create_appointment", summary: "Crear turno: Ana López", params: {} };

test("renders the summary and confirms via resumeChat, showing the receipt", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "✅ Turno creado: Ana López" };
      yield { type: "done" };
    })(),
  );
  render(<ConfirmCard threadId="t1" action={action} onClose={vi.fn()} />);

  expect(screen.getByText(/Crear turno: Ana López/)).toBeTruthy();
  fireEvent.click(screen.getByText("Confirmar"));

  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t1", "confirm"));
  await waitFor(() => expect(screen.getByText(/Turno creado/)).toBeTruthy());
});

test("cancel calls resumeChat with cancel", async () => {
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "Cancelado, no creé el turno." };
      yield { type: "done" };
    })(),
  );
  render(<ConfirmCard threadId="t1" action={action} onClose={vi.fn()} />);

  fireEvent.click(screen.getByText("Cancelar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t1", "cancel"));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (PowerShell): `cd frontend; npx vitest run components/ConfirmCard.test.tsx`
Expected: FAIL (cannot find `./ConfirmCard`).

- [ ] **Step 3: Create `ConfirmCard.tsx`**

Create `frontend/components/ConfirmCard.tsx`:

```tsx
"use client";

import { useState } from "react";
import { resumeChat, type ProposedAction } from "../lib/chatStream";

export function ConfirmCard({
  threadId,
  action,
  onClose,
}: {
  threadId: string;
  action: ProposedAction;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<"idle" | "working" | "done">("idle");
  const [receipt, setReceipt] = useState("");

  async function decide(decision: "confirm" | "cancel") {
    setPhase("working");
    let text = "";
    try {
      for await (const ev of resumeChat(threadId, decision)) {
        if (ev.type === "token") {
          text += ev.text;
          setReceipt(text);
        }
      }
    } catch (err) {
      setReceipt(err instanceof Error ? err.message : "No se pudo completar la acción.");
    }
    setPhase("done");
  }

  return (
    <div
      style={{
        border: "1px solid #c7c7c7",
        borderRadius: 8,
        padding: 12,
        margin: 12,
        background: "#fafafa",
      }}
    >
      <p style={{ fontWeight: 600, margin: "0 0 8px" }}>{action.summary}</p>
      {phase !== "done" ? (
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => decide("confirm")} disabled={phase === "working"}>
            Confirmar
          </button>
          <button onClick={() => decide("cancel")} disabled={phase === "working"}>
            Cancelar
          </button>
        </div>
      ) : (
        <div>
          <p style={{ margin: "0 0 8px" }}>{receipt}</p>
          <button onClick={onClose}>Cerrar</button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (PowerShell): `cd frontend; npx vitest run components/ConfirmCard.test.tsx`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire the runtime (`runtime.ts`)**

Replace the full contents of `frontend/lib/runtime.ts`:

```ts
"use client";

import { useMemo } from "react";
import {
  useLocalRuntime,
  type ChatModelAdapter,
  type ChatModelRunOptions,
  type ChatModelRunResult,
} from "@assistant-ui/react";
import type { ThreadUserMessage } from "@assistant-ui/react";
import { streamChat, type ProposedAction, type Source } from "./chatStream";

export interface PendingAction {
  threadId: string;
  action: ProposedAction;
}

function lastUserText(messages: ChatModelRunOptions["messages"]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "user") {
      const userMsg = msg as ThreadUserMessage;
      return userMsg.content.map((p) => (p.type === "text" ? p.text : "")).join("");
    }
  }
  return "";
}

function sourcesBlock(sources: Source[]): string {
  if (sources.length === 0) return "";
  const lines = sources.map(
    (s) => `[${s.n}] ${s.title}${s.page != null ? ` — p.${s.page}` : ""}`,
  );
  return `\n\n**Fuentes:**\n${lines.join("\n")}`;
}

export function useChatRuntime(onConfirm?: (p: PendingAction) => void) {
  const adapter = useMemo<ChatModelAdapter>(
    () => ({
      async *run({ messages, abortSignal }: ChatModelRunOptions): AsyncGenerator<ChatModelRunResult, void> {
        const query = lastUserText(messages);
        let answer = "";
        let sources: Source[] = [];

        try {
          for await (const ev of streamChat(query, abortSignal)) {
            if (ev.type === "token") {
              answer += ev.text;
              yield { content: [{ type: "text", text: answer }] };
            } else if (ev.type === "sources") {
              sources = ev.sources;
            } else if (ev.type === "confirm") {
              onConfirm?.({ threadId: ev.threadId, action: ev.action });
              yield {
                content: [{ type: "text", text: "📝 Propuse un turno — revisá la tarjeta de confirmación." }],
              };
              return;
            }
          }
        } catch (err) {
          if (abortSignal?.aborted) return;
          const message = err instanceof Error ? err.message : "No se pudo contactar al asistente.";
          yield { content: [{ type: "text", text: message }] };
          return;
        }

        yield { content: [{ type: "text", text: answer + sourcesBlock(sources) }] };
      },
    }),
    [onConfirm],
  );
  return useLocalRuntime(adapter);
}
```

- [ ] **Step 6: Wire the page (`page.tsx`)**

Replace the full contents of `frontend/app/page.tsx`:

```tsx
"use client";

import { useCallback, useState } from "react";
import { AssistantRuntimeProvider, Thread } from "@assistant-ui/react";
import { useChatRuntime, type PendingAction } from "../lib/runtime";
import { DropZone } from "../components/DropZone";
import { DocumentList } from "../components/DocumentList";
import { ConfirmCard } from "../components/ConfirmCard";

export default function Home() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const onConfirm = useCallback((p: PendingAction) => setPending(p), []);
  const runtime = useChatRuntime(onConfirm);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <main style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100vh" }}>
        <aside style={{ padding: 16, borderRight: "1px solid #ddd", overflowY: "auto" }}>
          <h1 style={{ fontSize: 18 }}>Praxia</h1>
          <DropZone onIngested={() => setRefreshKey((k) => k + 1)} />
          <h2 style={{ fontSize: 14, marginTop: 16 }}>Documentos</h2>
          <DocumentList refreshKey={refreshKey} />
        </aside>
        <section style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
          <div style={{ flex: 1, minHeight: 0 }}>
            <Thread />
          </div>
          {pending && (
            <ConfirmCard
              threadId={pending.threadId}
              action={pending.action}
              onClose={() => setPending(null)}
            />
          )}
        </section>
      </main>
    </AssistantRuntimeProvider>
  );
}
```

- [ ] **Step 7: Run full frontend suite + lint + build**

Run (PowerShell): `cd frontend; npx vitest run` then `npm run lint` then `npm run build`
Expected: tests verdes, lint sin errores, build OK.

- [ ] **Step 8: Commit**

```bash
git add frontend/components/ConfirmCard.tsx frontend/components/ConfirmCard.test.tsx frontend/lib/runtime.ts frontend/app/page.tsx
git commit -m "feat(front): ConfirmCard + wiring del HITL en runtime/page"
```

---

### Task 8: E2E (`-m llm`) + smoke/docs

**Files:**
- Create: `backend/tests/test_action_e2e_llm.py`
- Modify: `frontend/SMOKE.md`

**Interfaces:**
- Consumes: el grafo real con checkpointer (`build_graph(MemorySaver())`), `seed_demo`, Ollama `gemma4:12b`+`gemma4:e4b`.

- [ ] **Step 1: Write the e2e test**

Create `backend/tests/test_action_e2e_llm.py`:

```python
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


@pytest.mark.llm
@pytest.mark.integration
async def test_create_appointment_confirm_writes_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-confirm"}}
    before = await _count_appointments(pid)
    await graph.ainvoke(
        new_state(f"agendá un turno para {client['full_name']} mañana a las 10", pid, "e2e-confirm"),
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_appointment",)  # se abrió la confirmación
    await graph.ainvoke(Command(resume="confirm"), config)
    assert await _count_appointments(pid) == before + 1


@pytest.mark.llm
@pytest.mark.integration
async def test_create_appointment_cancel_writes_nothing() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-cancel"}}
    before = await _count_appointments(pid)
    await graph.ainvoke(
        new_state(f"agendá un turno para {client['full_name']} mañana a las 10", pid, "e2e-cancel"),
        config,
    )
    await graph.ainvoke(Command(resume="cancel"), config)
    assert await _count_appointments(pid) == before
```

- [ ] **Step 2: Run the e2e test (requires Ollama + Postgres)**

Ensure `docker compose up -d` and Ollama running with both models pulled.
Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_action_e2e_llm.py -m llm -q`
Expected: PASS (2 passed). If the router misroutes the phrase away from `action`, the assert `snap.next == ("confirm_appointment",)` fails — see Risks.

- [ ] **Step 3: Update the browser smoke checklist**

In `frontend/SMOKE.md`, replace the line about the write action hitting the stub with:

```markdown
- **Acción de escritura (HITL):** escribí `agendá un turno para <nombre de un cliente> mañana a las 10`.
  Esperado: aparece una **tarjeta de confirmación** con el resumen del turno (cliente, profesional,
  fecha/hora) y botones **Confirmar / Cancelar**.
  - **Confirmar** → recibo `✅ Turno creado: …`. Verificá en la DB:
    `docker compose exec -T postgres psql -U praxia -d praxia -c "SELECT client_id, start_at, status FROM appointments ORDER BY created_at DESC LIMIT 1;"`
  - **Cancelar** → `Cancelado, no creé el turno.` y la tabla `appointments` no crece.
  - Pedido irresoluble (`agendá un turno para Zzz`) → abstención cordial, SIN tarjeta.
```

- [ ] **Step 4: Final gates (no-llm + lint + types + front)**

Run:
- `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → verde
- `backend\.venv\Scripts\ruff check backend` ; `backend\.venv\Scripts\ruff format --check backend`
- `backend\.venv\Scripts\python -m mypy app --config-file backend/pyproject.toml` (desde `backend/`) → sin errores
- `cd frontend; npx vitest run; npm run lint; npm run build` → verde

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_action_e2e_llm.py frontend/SMOKE.md
git commit -m "test(e2e): -m llm create_appointment confirm/cancel + smoke de escritura"
```

---

## Self-Review

**1. Spec coverage**
- Reemplazo de `action_stub` por write-tool HITL → Tasks 4 (nodos) + 5 (transporte) + 7 (UI). ✓
- Decisión #1 (dos nodos, sin recompute al reanudar) → Task 4 + test `test_confirm_writes_appointment_exactly_once`. ✓
- `propose_appointment` (extracción args tipados + resolver determinístico, fail-closed) → Task 3. ✓
- `db.create_appointment` parametrizada + guarda de tenant → Task 2. ✓
- `proposed_action` en el state + config → Task 1. ✓
- Surface del `interrupt` por SSE (riesgo #1) + `/chat/resume` sin Ollama → Task 5. ✓
- Tarjeta mínima funcional sobre `useLocalRuntime` (riesgo #2; recibo en la card) → Task 7. ✓
- `thread_id` round-trip (en el evento `confirm`, usado por `resumeChat`) → Tasks 5/6. ✓
- Multi-tenant (resolución + INSERT scoped) → Tasks 2/3. ✓
- Testing no-llm (resolver, ciclo HITL, SSE) + `-m llm` e2e + smoke → Tasks 3/4/5/8. ✓
- Diferidos (log_interaction, Editar, audit/consents, slot-filling, memoria, MCP, canvas, tz) → no se construyen (ausentes del plan a propósito). ✓

**2. Placeholder scan**: sin "TBD"/"TODO"/"handle edge cases". Cada step de código trae el código completo; cada step de test trae el test completo. ✓

**3. Type consistency**:
- `create_appointment(...) -> dict` con claves `id`/`status` usadas por `_format_receipt(params, row)` (usa `row["status"]`) — ✓ consistente Task 2 ↔ Task 4.
- `proposed_action` con `{"kind","summary","params"}` y `params.start_at/end_at` como ISO str: producido en Task 3, consumido en Task 4 (`datetime.fromisoformat(params["start_at"])`) y serializado JSON en Task 5 — ✓.
- `route_after_propose -> "confirm_appointment" | END` y `_INTENT_TO_NODE["action"]="propose_appointment"` coinciden con los nombres de nodo en `build.py` (Task 4) — ✓.
- `ProposalResult(proposed_action, abstained, message, reason)` igual en Task 3 (def), Task 4 (`_fake_propose`) — ✓.
- `resumeChat(threadId, decision, signal?)` y `useChatRuntime(onConfirm?)` y `ConfirmCard({threadId, action, onClose})` consistentes Tasks 6↔7. ✓
- `ChatEvent.confirm` = `{type, threadId, action}` producido por `parseEvent` (Task 6) y consumido por el runtime (Task 7). ✓

## Riesgos (del spec, recordatorio para ejecución)
- **Riesgo #1 — forma del `__interrupt__` en `astream` multi-mode** (Task 5): el test `test_sse_stream` fija el contrato con un fake; si el shape real difiere, ajustar SOLO `_sse_event_stream` (y, si hiciera falta, usar `aget_state` como en `test_hitl_cycle`). El ciclo HITL (Task 4) ya valida la API real de interrupt/resume con `MemorySaver`.
- **Riesgo #2 — tarjeta con `useLocalRuntime`** (Task 7): el recibo se muestra dentro de la `ConfirmCard` (no se inyecta al hilo de assistant-ui) — implementable y testeable; el "append al hilo" más pulido queda para la migración de canvas (diferido).
- **`with_structured_output` para los args en Gemma local**: bajo riesgo (args tipados; ya funciona en router/juez). Si el e2e (Task 8) muestra `None`, fallback a `format=` JSON Schema de Ollama en `_extract`.
- **Router misrouting** ("¿atienden los domingos?" caso conocido): si Task 8 falla por ruteo, agregar el caso al ajuste de router (DSPy, Fase 2); no recae en este slice.
