# log_interaction + Write-Tool Registry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Praxia's 2nd write-tool — `log_interaction` — behind the existing HITL skeleton, by generalizing the action path into a write-tool **registry** dispatched by `kind`.

**Architecture:** Router stays coarse (`action`). A generic `propose_action` node classifies which tool (`classify_write_action`, e4b) and delegates to `REGISTRY[kind].propose` (12b extraction + deterministic resolver); a generic `confirm_action` node runs the `interrupt` and dispatches `REGISTRY[kind].write`/`.format_receipt`/`.cancel_message`. Each tool is a `WriteTool` descriptor. The `interactions` table (Blueprint §5.2) is ported.

**Tech Stack:** Python 3.12, FastAPI, LangGraph (`interrupt`/`Command`, Postgres checkpointer), asyncpg, Pydantic, `langchain-ollama` (`gemma4:12b` extract, `gemma4:e4b` classify). Frontend: Next.js + vitest (unchanged functionally).

## Global Constraints

- **Inference 100% local** via Ollama; **$0**; no outbound network beyond Ollama/Postgres/Qdrant local (CLAUDE.md §0).
- **Writes only** via parametrized tool **behind `interrupt` confirmation**; never LLM-generated SQL (CLAUDE.md §4).
- **Multi-tenant**: every query/resolution/insert filters by `practice_id`; writer re-checks with `EXISTS(... AND practice_id = $1)` (CLAUDE.md §0.5).
- **Commits clean**: no `Co-Authored-By: Claude`, no Claude/Anthropic attribution anywhere (CLAUDE.md §6).
- **No new dependencies.**
- **Gates** (run from repo root; Windows venv): no-llm suite stays green
  `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` (currently **130 passed**);
  lint `backend\.venv\Scripts\ruff check backend` + `backend\.venv\Scripts\ruff format backend`;
  types `backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
  (the `--config-file` flag is **mandatory**: without it, false-positive `asyncpg [import-untyped]`).
- **Environment for integration/llm tests**: `docker compose up -d` (Postgres/Qdrant) + apply the updated
  `backend/app/schema.sql` + `backend\.venv\Scripts\python backend\seed_demo.py`. `-m llm` also needs Ollama
  with `gemma4:12b` and `gemma4:e4b` pulled.
- **PowerShell**: no `cd x && y` chaining; backend binds `127.0.0.1`. Run the backend with
  `backend\.venv\Scripts\python backend\dev.py` (never `uvicorn` directly).
- Tests are async with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed). `backend/tests/__init__.py`
  puts `backend/` on `sys.path` (so `from app...` and `from seed_demo import seed_demo` resolve). mypy only
  checks `app/`, so test functions need not be fully typed; **ruff checks everything** (sort imports, no unused).

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/schema.sql` | DDL | **modify**: append `interactions` table (idempotent) |
| `backend/app/db.py` | parametrized DB writers | **modify**: add `log_interaction(...)` |
| `backend/app/agents/resolvers.py` | shared name→entity resolution | **create**: `resolve_single_client` + `ClientResolution` |
| `backend/app/agents/interaction_agent.py` | interaction extraction+resolution | **create**: `ProposedInteraction` + `propose_interaction` |
| `backend/app/agents/write_tools.py` | registry + classifier + adapters | **create**: `WriteTool`, `REGISTRY`, `classify_write_action`, `_write_*`, `format_*_receipt` |
| `backend/app/agents/action_agent.py` | appointment proposer | **unchanged** (only imported by the registry) |
| `backend/app/graph/nodes.py` | graph nodes (orchestration) | **modify**: `propose_action_node`/`confirm_action_node` (generic); drop `_format_receipt` + `create_appointment` import |
| `backend/app/graph/edges.py` | routing | **modify**: `action`→`propose_action`; `route_after_propose`→`confirm_action` |
| `backend/app/graph/build.py` | graph wiring | **modify**: register/rename the 2 nodes |
| `backend/tests/test_schema.py` | schema columns | **modify**: add interactions column test |
| `backend/tests/test_log_interaction.py` | writer integration | **create** |
| `backend/tests/test_resolvers.py` | resolver unit | **create** |
| `backend/tests/test_interaction_agent.py` | proposer unit | **create** |
| `backend/tests/test_write_tools.py` | registry/classifier/adapter unit | **create** |
| `backend/tests/test_nodes.py` | node unit | **modify**: rewrite propose tests; add unsupported test |
| `backend/tests/test_edges.py` | edges unit | **modify**: renamed targets |
| `backend/tests/test_graph.py` | graph unit | **modify**: renamed node names |
| `backend/tests/test_hitl_cycle.py` | interrupt/resume | **modify**: parametrize over kinds |
| `backend/tests/test_action_e2e_llm.py` | e2e | **modify**: rename asserts + add interaction tests |
| `frontend/components/ConfirmCard.test.tsx` | card contract | **modify (optional)**: interaction-shaped action |

---

## Task 1: `interactions` table (schema)

**Files:**
- Modify: `backend/app/schema.sql` (append after the `appointments` block, ~line 84)
- Test: `backend/tests/test_schema.py`

**Interfaces:**
- Produces: table `interactions(id, practice_id, client_id, practitioner_id, appointment_id, type, summary, content, occurred_at, source, created_at)` + index `idx_interactions_client`.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_schema.py`:

```python
@pytest.mark.integration
async def test_interactions_table_has_expected_columns() -> None:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'interactions'"
    )
    cols = {r["column_name"] for r in rows}
    assert {
        "practice_id",
        "client_id",
        "type",
        "summary",
        "content",
        "occurred_at",
        "source",
    } <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_schema.py::test_interactions_table_has_expected_columns -v`
Expected: FAIL (empty `cols` set — table does not exist yet).

- [ ] **Step 3: Add the DDL** — append to `backend/app/schema.sql`:

```sql

-- ====== Interacciones (el corazón del CRM de atención) ======
CREATE TABLE IF NOT EXISTS interactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id     UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    practitioner_id UUID REFERENCES practitioners(id),
    appointment_id  UUID REFERENCES appointments(id),
    type            TEXT NOT NULL CHECK (type IN ('sesion','llamada','email','nota','mensaje')),
    summary         TEXT,
    content         TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','agente','import')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_interactions_client ON interactions(client_id, occurred_at DESC);
```

- [ ] **Step 4: Apply the schema to the running Postgres**

Run (Git Bash): `docker compose exec -T postgres psql -U praxia -d praxia < backend/app/schema.sql`
(PowerShell alt: `Get-Content backend\app\schema.sql -Raw | docker compose exec -T postgres psql -U praxia -d praxia`)
Expected: `CREATE TABLE` / `CREATE INDEX` (or no error if re-applied; idempotent).

- [ ] **Step 5: Run test to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_schema.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schema.sql backend/tests/test_schema.py
git commit -m "feat(schema): add interactions table (Blueprint §5.2)"
```

---

## Task 2: `db.log_interaction` writer

**Files:**
- Modify: `backend/app/db.py` (add after `create_appointment`, ~line 200)
- Test: `backend/tests/test_log_interaction.py`

**Interfaces:**
- Consumes: table `interactions` (Task 1); `get_pool()`.
- Produces: `async def log_interaction(practice_id: str, client_id: str, *, type: str, summary: str | None = None, content: str | None = None, occurred_at: datetime, source: str = "agente") -> dict[str, Any]` returning `{"id": str, "occurred_at": datetime, "type": str}`; raises `RuntimeError` if `client_id` is foreign/nonexistent.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_log_interaction.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app import db
from app.config import get_settings


@pytest.mark.integration
async def test_log_interaction_inserts_and_returns_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]
    row = await db.log_interaction(
        pid,
        client["id"],
        type="llamada",
        summary="Confirmó el turno",
        content="Llamé al cliente y confirmó.",
        occurred_at=datetime.now(UTC),
    )
    assert row["type"] == "llamada"
    assert row["id"]


@pytest.mark.integration
async def test_log_interaction_rejects_foreign_client() -> None:
    pid = get_settings().practice_id
    with pytest.raises(RuntimeError):
        await db.log_interaction(
            pid,
            str(uuid4()),
            type="nota",
            summary="x",
            content="y",
            occurred_at=datetime.now(UTC),
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_log_interaction.py -v`
Expected: FAIL with `AttributeError: module 'app.db' has no attribute 'log_interaction'`.

- [ ] **Step 3: Implement** — add to `backend/app/db.py`:

```python
async def log_interaction(
    practice_id: str,
    client_id: str,
    *,
    type: str,
    summary: str | None = None,
    content: str | None = None,
    occurred_at: datetime,
    source: str = "agente",
) -> dict[str, Any]:
    """Tool de escritura parametrizada: registra una interacción con un cliente.
    Verifica que el cliente pertenezca a la práctica (defensa en profundidad sobre
    el resolver) y recién inserta."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO interactions
            (practice_id, client_id, type, summary, content, occurred_at, source)
        SELECT $1, $2, $3, $4, $5, $6, $7
        WHERE EXISTS (SELECT 1 FROM clients WHERE id = $2 AND practice_id = $1)
        RETURNING id::text, occurred_at, type
        """,
        practice_id,
        client_id,
        type,
        summary,
        content,
        occurred_at,
        source,
    )
    if row is None:
        raise RuntimeError("log_interaction: cliente fuera de la práctica o inexistente")
    return dict(row)
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_log_interaction.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_log_interaction.py
git commit -m "feat(db): log_interaction parametrized writer with tenant guard"
```

---

## Task 3: shared `resolve_single_client`

**Files:**
- Create: `backend/app/agents/resolvers.py`
- Test: `backend/tests/test_resolvers.py`

**Interfaces:**
- Consumes: `db.find_clients_by_name(practice_id, name, *, limit) -> list[dict]`.
- Produces: `@dataclass ClientResolution(client: dict[str, Any] | None, abstain_message: str, abstain_reason: str)` and `async def resolve_single_client(practice_id: str, name: str, *, limit: int) -> ClientResolution`. `abstain_reason ∈ {"ok","client_missing","client_not_found","client_ambiguous"}`; `client` is set iff exactly one match.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_resolvers.py`:

```python
from app import db
from app.agents import resolvers


def _patch(monkeypatch, clients):  # type: ignore[no-untyped-def]
    async def _find(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    monkeypatch.setattr(db, "find_clients_by_name", _find)


async def test_resolves_single(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana"}])
    r = await resolvers.resolve_single_client("pid", "Ana", limit=5)
    assert r.client == {"id": "c1", "full_name": "Ana"}
    assert r.abstain_reason == "ok"


async def test_empty_name_is_missing(monkeypatch) -> None:
    r = await resolvers.resolve_single_client("pid", "  ", limit=5)
    assert r.client is None and r.abstain_reason == "client_missing"


async def test_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [])
    r = await resolvers.resolve_single_client("pid", "Zzz", limit=5)
    assert r.client is None and r.abstain_reason == "client_not_found"
    assert "Zzz" in r.abstain_message


async def test_ambiguous(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}])
    r = await resolvers.resolve_single_client("pid", "Ana", limit=5)
    assert r.client is None and r.abstain_reason == "client_ambiguous"
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_resolvers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agents.resolvers'`.

- [ ] **Step 3: Implement** — create `backend/app/agents/resolvers.py`:

```python
from dataclasses import dataclass
from typing import Any

from app import db


@dataclass
class ClientResolution:
    client: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str


async def resolve_single_client(practice_id: str, name: str, *, limit: int) -> ClientResolution:
    """Resuelve un nombre a un único cliente de la práctica. Fail-closed: vacío /
    no encontrado / ambiguo → sin cliente y con mensaje de abstención cordial."""
    if not name.strip():
        return ClientResolution(
            None, "¿Sobre qué cliente es? Decime el nombre.", "client_missing"
        )
    clients = await db.find_clients_by_name(practice_id, name, limit=limit)
    if not clients:
        return ClientResolution(
            None,
            f"No encontré ningún cliente que coincida con «{name}». ¿Me das el nombre completo?",
            "client_not_found",
        )
    if len(clients) > 1:
        names = ", ".join(c["full_name"] for c in clients)
        return ClientResolution(
            None,
            f"Hay varios clientes que coinciden con «{name}»: {names}. ¿Cuál es?",
            "client_ambiguous",
        )
    return ClientResolution(clients[0], "", "ok")
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_resolvers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/resolvers.py backend/tests/test_resolvers.py
git commit -m "feat(agents): shared resolve_single_client helper"
```

---

## Task 4: `interaction_agent` — proposer

**Files:**
- Create: `backend/app/agents/interaction_agent.py`
- Test: `backend/tests/test_interaction_agent.py`

**Interfaces:**
- Consumes: `resolve_single_client` (Task 3); `ProposalResult` from `app.agents.action_agent`; `make_llm`, `get_settings`; `settings.appt_name_match_limit`.
- Produces: `class ProposedInteraction(BaseModel)` with `client_name: str`, `type: Literal["sesion","llamada","email","nota","mensaje"] = "nota"`, `summary: str`, `content: str`; and `async def propose_interaction(question: str, practice_id: str, *, now: datetime, gen_llm: Any = None) -> ProposalResult`. On success `proposed_action = {"kind": "log_interaction", "summary": <card text>, "params": {client_id, client_name, type, summary, content, occurred_at(ISO), source="agente"}}`.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_interaction_agent.py`:

```python
from datetime import UTC, datetime

from app import db
from app.agents import interaction_agent
from app.agents.interaction_agent import ProposedInteraction

NOW = datetime(2026, 6, 28, 14, 30, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedInteraction) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedInteraction) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _patch_clients(monkeypatch, clients):  # type: ignore[no-untyped-def]
    async def _find(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    monkeypatch.setattr(db, "find_clients_by_name", _find)


async def test_happy_path_builds_action(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])
    llm = FakeGenLLM(
        ProposedInteraction(
            client_name="Ana",
            type="llamada",
            summary="Ana confirmó el turno",
            content="Llamé a Ana y confirmó el turno del martes.",
        )
    )
    result = await interaction_agent.propose_interaction(
        "registrá que llamé a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None
    assert pa["kind"] == "log_interaction"
    p = pa["params"]
    assert p["client_id"] == "c1"
    assert p["type"] == "llamada"
    assert p["summary"] == "Ana confirmó el turno"
    assert p["content"].startswith("Llamé a Ana")
    assert p["occurred_at"] == "2026-06-28T14:30:00+00:00"
    assert p["source"] == "agente"
    assert "Ana López" in pa["summary"]
    assert pa["summary"] != p["summary"]  # card text vs DB column


async def test_default_type_is_nota(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", summary="s", content="c"))
    result = await interaction_agent.propose_interaction("anotá algo de Ana", "pid", now=NOW, gen_llm=llm)
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["type"] == "nota"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch_clients(monkeypatch, [])
    llm = FakeGenLLM(ProposedInteraction(client_name="Zzz", summary="s", content="c"))
    result = await interaction_agent.propose_interaction("registrá algo de Zzz", "pid", now=NOW, gen_llm=llm)
    assert result.abstained and result.reason == "client_not_found"
    assert "Zzz" in result.message


async def test_abstains_client_ambiguous(monkeypatch) -> None:
    _patch_clients(
        monkeypatch,
        [{"id": "c1", "full_name": "Ana López"}, {"id": "c2", "full_name": "Ana Pérez"}],
    )
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", summary="s", content="c"))
    result = await interaction_agent.propose_interaction("registrá algo de Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained and result.reason == "client_ambiguous"


async def test_abstains_client_empty(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])
    llm = FakeGenLLM(ProposedInteraction(client_name="   ", summary="s", content="c"))
    result = await interaction_agent.propose_interaction("registrá algo", "pid", now=NOW, gen_llm=llm)
    assert result.abstained and result.reason == "client_missing"


async def test_abstains_when_extract_fails() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await interaction_agent.propose_interaction("registrá", "pid", now=NOW, gen_llm=_LLM())
    assert result.abstained and result.reason == "extract_failed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_interaction_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agents.interaction_agent'`.

- [ ] **Step 3: Implement** — create `backend/app/agents/interaction_agent.py`:

```python
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.action_agent import ProposalResult
from app.agents.resolvers import resolve_single_client
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude registrar la interacción con esos datos. ¿Probás de nuevo indicando "
    "el cliente y qué pasó?"
)


class ProposedInteraction(BaseModel):
    client_name: str
    type: Literal["sesion", "llamada", "email", "nota", "mensaje"] = "nota"
    summary: str
    content: str


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt() -> str:
    return (
        "Sos el asistente que registra interacciones con clientes de una práctica "
        "profesional. A partir del pedido del usuario, extraé los datos de UNA interacción "
        "ya ocurrida. Inferí 'type' de la acción mencionada: 'llamé'→llamada, 'mandé un "
        "email'→email, 'tuvimos una sesión'→sesion, 'le mandé un mensaje'→mensaje; si no es "
        "claro, usá 'nota'. Escribí 'summary' como un resumen de UNA línea y poné en 'content' "
        "el texto completo de lo que hay que registrar. 'client_name' es la persona involucrada."
    )


async def _extract(question: str, gen_llm: Any) -> ProposedInteraction | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedInteraction)
    try:
        result = await structured.ainvoke(
            [("system", _system_prompt()), ("human", question)]
        )
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedInteraction) else None


def _card_summary(client_name: str, type_: str, summary: str) -> str:
    snippet = summary.strip()
    if len(snippet) > 80:
        snippet = snippet[:79] + "…"
    return f"Registrar {type_} de {client_name} — «{snippet}»"


async def propose_interaction(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, gen_llm)
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

    params: dict[str, Any] = {
        "client_id": client["id"],
        "client_name": client["full_name"],
        "type": extracted.type,
        "summary": extracted.summary,
        "content": extracted.content,
        "occurred_at": now.isoformat(),
        "source": "agente",
    }
    proposed_action = {
        "kind": "log_interaction",
        "summary": _card_summary(client["full_name"], extracted.type, extracted.summary),
        "params": params,
    }
    return ProposalResult(
        proposed_action=proposed_action, abstained=False, message="", reason="ok"
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_interaction_agent.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/interaction_agent.py backend/tests/test_interaction_agent.py
git commit -m "feat(agents): interaction_agent — ProposedInteraction + propose_interaction"
```

---

## Task 5: write-tool `REGISTRY` + classifier + adapters

**Files:**
- Create: `backend/app/agents/write_tools.py`
- Test: `backend/tests/test_write_tools.py`

**Interfaces:**
- Consumes: `db.create_appointment`, `db.log_interaction`; `propose_appointment` (action_agent), `propose_interaction` (interaction_agent); `ProposalResult`; `make_llm`.
- Produces:
  - `@dataclass(frozen=True) class WriteTool(kind: str, propose, write, format_receipt, cancel_message)`.
  - `REGISTRY: dict[str, WriteTool]` with keys `"create_appointment"`, `"log_interaction"`.
  - `async def classify_write_action(question: str, llm: Any = None) -> str` → one of `"create_appointment" | "log_interaction" | "unsupported"`.
  - module-level adapters `_write_appointment`, `_write_interaction` (`(practice_id, params) -> row`) and `format_appointment_receipt`, `format_interaction_receipt` (`(params, row) -> str`).

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_write_tools.py`:

```python
from datetime import UTC, datetime

from app.agents import write_tools
from app.agents.write_tools import REGISTRY, WriteActionDecision, classify_write_action


class _FakeStructured:
    def __init__(self, value: WriteActionDecision) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeClassifyLLM:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(WriteActionDecision(kind=self._kind))


async def test_classify_returns_kind() -> None:
    assert (
        await classify_write_action("registrá que llamé a Ana", llm=FakeClassifyLLM("log_interaction"))
        == "log_interaction"
    )
    assert (
        await classify_write_action("agendá un turno", llm=FakeClassifyLLM("create_appointment"))
        == "create_appointment"
    )
    assert (
        await classify_write_action("cancelá el turno", llm=FakeClassifyLLM("unsupported"))
        == "unsupported"
    )


def test_registry_has_both_tools() -> None:
    assert set(REGISTRY) == {"create_appointment", "log_interaction"}
    for kind, tool in REGISTRY.items():
        assert tool.kind == kind
        assert tool.cancel_message


async def test_write_interaction_adapter_maps_params(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_log(practice_id, client_id, *, type, summary, content, occurred_at, source):  # type: ignore[no-untyped-def]
        captured.update(
            practice_id=practice_id,
            client_id=client_id,
            type=type,
            summary=summary,
            content=content,
            occurred_at=occurred_at,
            source=source,
        )
        return {"id": "i1", "occurred_at": occurred_at, "type": type}

    monkeypatch.setattr(write_tools.db, "log_interaction", _fake_log)
    params = {
        "client_id": "c1",
        "client_name": "Ana López",
        "type": "llamada",
        "summary": "Ana confirmó",
        "content": "Llamé a Ana.",
        "occurred_at": "2026-06-28T14:30:00+00:00",
        "source": "agente",
    }
    row = await write_tools._write_interaction("pid", params)
    assert captured["client_id"] == "c1"
    assert captured["type"] == "llamada"
    assert captured["occurred_at"] == datetime(2026, 6, 28, 14, 30, tzinfo=UTC)  # ISO→datetime
    assert "client_name" not in captured  # display-only key dropped
    assert row["id"] == "i1"


def test_interaction_receipt_is_deterministic() -> None:
    params = {
        "client_name": "Ana López",
        "type": "llamada",
        "occurred_at": "2026-06-28T14:30:00+00:00",
    }
    msg = write_tools.format_interaction_receipt(params, {"id": "i1"})
    assert "✅" in msg and "llamada" in msg and "Ana López" in msg
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agents.write_tools'`.

- [ ] **Step 3: Implement** — create `backend/app/agents/write_tools.py`:

```python
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app import db
from app.agents.action_agent import ProposalResult, propose_appointment
from app.agents.interaction_agent import propose_interaction
from app.llm import make_llm


@dataclass(frozen=True)
class WriteTool:
    kind: str
    propose: Callable[..., Awaitable[ProposalResult]]
    write: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    format_receipt: Callable[[dict[str, Any], dict[str, Any]], str]
    cancel_message: str


# ---- create_appointment ----
async def _write_appointment(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    return await db.create_appointment(
        practice_id,
        params["client_id"],
        params["practitioner_id"],
        datetime.fromisoformat(params["start_at"]),
        datetime.fromisoformat(params["end_at"]),
        reason=params.get("reason"),
        channel=params.get("channel"),
        status=params.get("status", "programado"),
    )


def format_appointment_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    start = datetime.fromisoformat(params["start_at"])
    return (
        f"✅ Turno creado: {params['client_name']} con {params['practitioner_name']} "
        f"el {start.strftime('%d/%m %H:%M')} (UTC) (estado: {row['status']})."
    )


# ---- log_interaction ----
async def _write_interaction(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    return await db.log_interaction(
        practice_id,
        params["client_id"],
        type=params["type"],
        summary=params.get("summary"),
        content=params.get("content"),
        occurred_at=datetime.fromisoformat(params["occurred_at"]),
        source=params.get("source", "agente"),
    )


def format_interaction_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    occurred = datetime.fromisoformat(params["occurred_at"])
    return (
        f"✅ Interacción registrada: {params['type']} de {params['client_name']} "
        f"({occurred.strftime('%d/%m %H:%M')} UTC)."
    )


REGISTRY: dict[str, WriteTool] = {
    "create_appointment": WriteTool(
        kind="create_appointment",
        propose=propose_appointment,
        write=_write_appointment,
        format_receipt=format_appointment_receipt,
        cancel_message="Cancelado, no creé el turno.",
    ),
    "log_interaction": WriteTool(
        kind="log_interaction",
        propose=propose_interaction,
        write=_write_interaction,
        format_receipt=format_interaction_receipt,
        cancel_message="Cancelado, no registré la interacción.",
    ),
}


class WriteActionDecision(BaseModel):
    kind: Literal["create_appointment", "log_interaction", "unsupported"]


CLASSIFY_PROMPT = (
    "Sos el despachador de acciones de escritura de un CRM de prácticas profesionales. "
    "El usuario pidió ejecutar UNA acción que modifica datos. Clasificá QUÉ acción es:\n"
    "- create_appointment: agendar/crear un turno o cita. "
    'Ej: "agendá un turno para Ana mañana 10", "dale una cita a Juan el martes".\n'
    "- log_interaction: registrar/anotar una interacción ya ocurrida con un cliente "
    "(sesión, llamada, email, nota, mensaje). "
    'Ej: "registrá que llamé a Ana", "anotá una nota sobre Juan", "guardá que le mandé un email".\n'
    "- unsupported: cualquier otra acción de escritura que NO sea esas dos (cancelar/editar/"
    "reprogramar un turno, dar de baja un cliente, facturar). "
    'Ej: "cancelá el turno de Juan", "editá la cita".\n'
    "Respondé solo con la opción."
)


def _classify_llm() -> Any:
    return make_llm("gemma4:e4b", temperature=0.0)


async def classify_write_action(question: str, llm: Any = None) -> str:
    llm = llm or _classify_llm()
    structured = llm.with_structured_output(WriteActionDecision)
    decision: WriteActionDecision = await structured.ainvoke(
        [("system", CLASSIFY_PROMPT), ("human", question)]
    )
    return decision.kind
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/agents/write_tools.py backend/tests/test_write_tools.py
git commit -m "feat(agents): write-tool registry + classify_write_action + adapters"
```

---

## Task 6: generic graph nodes (rename + dispatch)

**Files:**
- Modify: `backend/app/graph/nodes.py`, `backend/app/graph/edges.py`, `backend/app/graph/build.py`
- Test: `backend/tests/test_nodes.py`, `backend/tests/test_edges.py`, `backend/tests/test_graph.py`

**Interfaces:**
- Consumes: `REGISTRY`, `classify_write_action` (Task 5).
- Produces: `propose_action_node`, `confirm_action_node` (replace `propose_appointment_node`/`confirm_appointment_node`); `edges._INTENT_TO_NODE["action"] == "propose_action"`; `route_after_propose -> "confirm_action" | END`; graph nodes named `"propose_action"`/`"confirm_action"`.

- [ ] **Step 1: Update the routing/graph tests first (red)** — edit:

`backend/tests/test_edges.py` — replace the three asserts:
```python
def test_action_intent_routes_to_propose() -> None:
    assert _INTENT_TO_NODE["action"] == "propose_action"


def test_route_after_propose_to_confirm_when_action_present() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = {"kind": "create_appointment"}
    assert route_after_propose(state) == "confirm_action"


def test_route_after_propose_to_end_when_abstained() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = None
    assert route_after_propose(state) == END
```

`backend/tests/test_graph.py` — in `test_route_maps_intents_to_nodes` change the action line, and in `test_every_intent_maps_to_a_real_node` change the node set:
```python
    assert edges.route({"intent": "action"}) == "propose_action"  # type: ignore[arg-type]
```
```python
    valid_nodes = {"rag", "chitchat", "scope_reject", "sql_node", "propose_action"}
```

`backend/tests/test_nodes.py` — **replace** `test_propose_node_abstains_emits_message` with these three tests:
```python
async def test_propose_action_unsupported_emits_capabilities(monkeypatch):
    async def _clf(question, llm=None):
        return "unsupported"

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    tokens, sources = await _run(nodes.propose_action_node, new_state("cancelá el turno", "p", "t"))
    assert "agendar turnos" in tokens and "registrar interacciones" in tokens
    assert sources == []


async def test_propose_action_abstains_from_tool(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import ProposalResult

    async def _clf(question, llm=None):
        return "create_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None):
        return ProposalResult(
            proposed_action=None,
            abstained=True,
            message="No encontré al cliente.",
            reason="client_not_found",
        )

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "create_appointment",
        write_tools.WriteTool(
            kind="create_appointment",
            propose=_propose,
            write=write_tools._write_appointment,
            format_receipt=write_tools.format_appointment_receipt,
            cancel_message="x",
        ),
    )
    tokens, sources = await _run(nodes.propose_action_node, new_state("agendá", "p", "t"))
    assert tokens == "No encontré al cliente."
    assert sources == []


async def test_propose_action_happy_returns_action_without_emitting(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import ProposalResult

    action = {"kind": "create_appointment", "summary": "s", "params": {}}

    async def _clf(question, llm=None):
        return "create_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None):
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "create_appointment",
        write_tools.WriteTool(
            kind="create_appointment",
            propose=_propose,
            write=write_tools._write_appointment,
            format_receipt=write_tools.format_appointment_receipt,
            cancel_message="x",
        ),
    )
    tokens, sources = await _run(nodes.propose_action_node, new_state("agendá", "p", "t"))
    assert tokens == ""  # camino feliz: no emite (la tarjeta sale del interrupt)
    assert sources == []
```

- [ ] **Step 2: Run the edited tests to verify they fail**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_edges.py backend/tests/test_graph.py backend/tests/test_nodes.py -q`
Expected: FAIL (`AttributeError: ... 'propose_action_node'` / assertion errors on old names).

- [ ] **Step 3: Implement `edges.py`** — replace the body:

```python
from langgraph.graph import END

from app.graph.state import AgentState

_INTENT_TO_NODE = {
    "rag": "rag",
    "sql": "sql_node",
    "action": "propose_action",
    "chitchat": "chitchat",
    "out_of_scope": "scope_reject",
}


def route(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state["intent"], "scope_reject")


def route_after_propose(state: AgentState) -> str:
    return "confirm_action" if state.get("proposed_action") else END
```

- [ ] **Step 4: Implement `nodes.py`** — (a) fix imports: remove `from app.agents.action_agent import propose_appointment` and `from app.db import create_appointment`; add `from app.agents.write_tools import REGISTRY, classify_write_action`. Keep `from datetime import UTC, datetime` and `from langgraph.types import interrupt`. (b) delete `_format_receipt`. (c) replace the two node functions:

```python
async def propose_action_node(state: AgentState) -> dict:
    question = last_user_text(state)
    try:
        kind = await classify_write_action(question)
    except Exception:  # noqa: BLE001 - fail-closed: si el clasificador falla, no adivinamos
        kind = "unsupported"
    if kind not in REGISTRY:
        msg = (
            "Por ahora puedo agendar turnos o registrar interacciones. "
            "¿Cuál de las dos necesitás?"
        )
        write_token(msg)
        write_sources([])
        return {"proposed_action": None, "sources": [], "messages": [AIMessage(content=msg)]}
    result = await REGISTRY[kind].propose(question, state["practice_id"], now=datetime.now(UTC))
    if result.abstained:
        write_token(result.message)
        write_sources([])
        return {
            "proposed_action": None,
            "sources": [],
            "messages": [AIMessage(content=result.message)],
        }
    return {"proposed_action": result.proposed_action}


async def confirm_action_node(state: AgentState) -> dict:
    action = state["proposed_action"] or {}
    tool = REGISTRY[action["kind"]]
    decision = interrupt(action)
    if decision == "confirm":
        row = await tool.write(state["practice_id"], action["params"])
        msg = tool.format_receipt(action["params"], row)
    else:
        msg = tool.cancel_message
    write_token(msg)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=msg)]}
```

> Note: `datetime`/`UTC` stay imported (used by `propose_action_node`). The `_format_receipt` logic now lives in `write_tools.format_appointment_receipt` (Task 5).

- [ ] **Step 5: Implement `build.py`** — update the node imports, `_LEAF_NODES`, `add_node`, and conditional edges:

```python
from app.graph.nodes import (
    chitchat_node,
    confirm_action_node,
    propose_action_node,
    rag_node,
    scope_reject_node,
    sql_node,
)
```
```python
_LEAF_NODES = ("rag", "chitchat", "scope_reject", "sql_node", "confirm_action")
```
```python
    g.add_node("propose_action", propose_action_node)
    g.add_node("confirm_action", confirm_action_node)
```
```python
    g.add_conditional_edges(
        "router",
        route,
        {
            "rag": "rag",
            "chitchat": "chitchat",
            "scope_reject": "scope_reject",
            "sql_node": "sql_node",
            "propose_action": "propose_action",
        },
    )
    g.add_conditional_edges(
        "propose_action",
        route_after_propose,
        {"confirm_action": "confirm_action", END: END},
    )
```

- [ ] **Step 6: Verify no stale references remain**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_edges.py backend/tests/test_graph.py backend/tests/test_nodes.py -q`
Expected: PASS. Then grep for leftovers:
Run (Git Bash): `grep -rn "propose_appointment_node\|confirm_appointment_node\|_format_receipt" backend/app`
Expected: no matches in `backend/app`.

- [ ] **Step 7: Run the full no-llm suite (catch ripples)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS. (`test_hitl_cycle.py` still references old node names → it will FAIL here; that's expected and fixed in Task 7. If it fails ONLY there, proceed; otherwise fix the ripple before committing.)

> Because `test_hitl_cycle.py` is rewritten in Task 7, run this Step-7 suite **excluding** it to confirm everything else is green:
> `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q --ignore=backend/tests/test_hitl_cycle.py`
> Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/graph/nodes.py backend/app/graph/edges.py backend/app/graph/build.py backend/tests/test_edges.py backend/tests/test_graph.py backend/tests/test_nodes.py
git commit -m "refactor(graph): generic propose_action/confirm_action dispatch by kind"
```

---

## Task 7: HITL cycle parametrized over kinds

**Files:**
- Modify (rewrite): `backend/tests/test_hitl_cycle.py`

**Interfaces:**
- Consumes: `nodes.propose_action_node`/`confirm_action_node`, `nodes.classify_write_action`, `write_tools.REGISTRY`, `WriteTool`, `edges.route_after_propose` (Task 6).

- [ ] **Step 1: Rewrite the test file** — replace the entire contents of `backend/tests/test_hitl_cycle.py`:

```python
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents import write_tools
from app.agents.action_agent import ProposalResult
from app.agents.write_tools import WriteTool
from app.graph import edges, nodes
from app.graph.state import AgentState, new_state

APPOINTMENT = {
    "kind": "create_appointment",
    "summary": "Crear turno: Ana López con Dra. Gómez — 30/06 10:00–10:30",
    "params": {"client_id": "c1"},
}
INTERACTION = {
    "kind": "log_interaction",
    "summary": "Registrar llamada de Ana López — «confirmó el turno»",
    "params": {"client_id": "c1"},
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
    g.add_node("propose_action", nodes.propose_action_node)
    g.add_node("confirm_action", nodes.confirm_action_node)
    g.add_edge(START, "propose_action")
    g.add_conditional_edges(
        "propose_action",
        edges.route_after_propose,
        {"confirm_action": "confirm_action", END: END},
    )
    g.add_edge("confirm_action", END)
    return g.compile(checkpointer=MemorySaver())


def _install(monkeypatch, kind, action, write_spy):  # type: ignore[no-untyped-def]
    async def _clf(question, llm=None):  # type: ignore[no-untyped-def]
        return kind

    async def _propose(question, practice_id, *, now, gen_llm=None):  # type: ignore[no-untyped-def]
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        kind,
        WriteTool(
            kind=kind,
            propose=_propose,
            write=write_spy,
            format_receipt=lambda params, row: "✅ ok",
            cancel_message="cancelado",
        ),
    )


@pytest.mark.parametrize(
    "kind,action",
    [("create_appointment", APPOINTMENT), ("log_interaction", INTERACTION)],
)
async def test_confirm_writes_exactly_once(monkeypatch, kind, action) -> None:
    spy = _Spy({"id": "row-1", "status": "programado", "occurred_at": None, "type": "llamada"})
    _install(monkeypatch, kind, action, spy)
    graph = _hitl_graph()
    tid = f"t-confirm-{kind}"
    config = {"configurable": {"thread_id": tid}}

    await graph.ainvoke(new_state("hacé algo", "pid", tid), config)
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_action",)
    assert snap.tasks[0].interrupts[0].value["kind"] == kind
    assert spy.calls == []  # nada escrito todavía

    await graph.ainvoke(Command(resume="confirm"), config)
    assert len(spy.calls) == 1  # se escribió UNA vez (sin recomputar la propuesta)


@pytest.mark.parametrize(
    "kind,action",
    [("create_appointment", APPOINTMENT), ("log_interaction", INTERACTION)],
)
async def test_cancel_writes_nothing(monkeypatch, kind, action) -> None:
    spy = _Spy({"id": "row-1"})
    _install(monkeypatch, kind, action, spy)
    graph = _hitl_graph()
    tid = f"t-cancel-{kind}"
    config = {"configurable": {"thread_id": tid}}

    await graph.ainvoke(new_state("hacé algo", "pid", tid), config)
    await graph.ainvoke(Command(resume="cancel"), config)
    assert spy.calls == []
```

- [ ] **Step 2: Run to verify it passes**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_hitl_cycle.py -v`
Expected: PASS (4 parametrized tests: confirm/cancel × 2 kinds).

- [ ] **Step 3: Run the full no-llm suite (now fully green)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (≥ previous 130; new unit tests added).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_hitl_cycle.py
git commit -m "test(hitl): parametrize interrupt/resume cycle over write-tool kinds"
```

---

## Task 8: e2e (`-m llm`) — log_interaction + classification

**Files:**
- Modify: `backend/tests/test_action_e2e_llm.py`

**Interfaces:**
- Consumes: real graph (`build_graph`), Ollama (`gemma4:12b` + `gemma4:e4b`), Postgres + seed, `interactions` table (Task 1).

- [ ] **Step 1: Update the existing appointment asserts (rename) + add interaction tests** — in `backend/tests/test_action_e2e_llm.py`:

(a) In **both** existing tests, change `assert snap.next == ("confirm_appointment",)` → `assert snap.next == ("confirm_action",)`. In `test_create_appointment_confirm_writes_row`, also add right after that line:
```python
    assert snap.tasks[0].interrupts[0].value["kind"] == "create_appointment"  # clasificó bien
```

(b) Add the interactions count helper (next to `_count_appointments`):
```python
async def _count_interactions(pid: str) -> int:
    pool = await db.get_pool()
    return await pool.fetchval("SELECT count(*) FROM interactions WHERE practice_id = $1", pid)
```

(c) Append two new tests:
```python
@pytest.mark.llm
@pytest.mark.integration
async def test_log_interaction_confirm_writes_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-int-confirm"}}
    before = await _count_interactions(pid)
    await graph.ainvoke(
        new_state(
            f"registrá que llamé a {client['full_name']} y confirmó el turno del martes",
            pid,
            "e2e-int-confirm",
        ),
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_action",)
    assert snap.tasks[0].interrupts[0].value["kind"] == "log_interaction"  # clasificó bien
    await graph.ainvoke(Command(resume="confirm"), config)
    assert await _count_interactions(pid) == before + 1


@pytest.mark.llm
@pytest.mark.integration
async def test_log_interaction_cancel_writes_nothing() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "e2e-int-cancel"}}
    before = await _count_interactions(pid)
    await graph.ainvoke(
        new_state(
            f"registrá que le mandé un email a {client['full_name']}",
            pid,
            "e2e-int-cancel",
        ),
        config,
    )
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_action",)
    await graph.ainvoke(Command(resume="cancel"), config)
    assert await _count_interactions(pid) == before
```

- [ ] **Step 2: Run the e2e suite** (needs Ollama + Postgres + seed; schema already applied in Task 1)

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_action_e2e_llm.py -m llm -q`
Expected: PASS (4 tests: appointment confirm/cancel + interaction confirm/cancel).

> If the classifier mis-routes the interaction phrase (e.g. `kind == "create_appointment"`), the `assert ... == "log_interaction"` fails honestly — tighten `CLASSIFY_PROMPT` wording (Task 5) and add the failing phrase as a comment for a future DSPy golden case; do **not** weaken the assert.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_action_e2e_llm.py
git commit -m "test(e2e): log_interaction confirm/cancel + classification (-m llm)"
```

---

## Task 9 (optional): frontend ConfirmCard contract

**Files:**
- Modify: `frontend/components/ConfirmCard.test.tsx`

**Why optional:** the front is functionally unchanged (the card renders `action.summary`; `ProposedAction` is `{kind, summary, params}`, fully generic). This test only **locks** that the card is kind-agnostic.

- [ ] **Step 1: Add a contract test** — append to `frontend/components/ConfirmCard.test.tsx`:

```tsx
test("renders a log_interaction action summary (kind-agnostic card)", async () => {
  const interaction = {
    kind: "log_interaction",
    summary: "Registrar llamada de Ana López — «confirmó el turno»",
    params: {},
  };
  vi.spyOn(chatStream, "resumeChat").mockImplementation(() =>
    (async function* () {
      yield { type: "token", text: "✅ Interacción registrada: llamada de Ana López" };
      yield { type: "done" };
    })(),
  );
  render(<ConfirmCard threadId="t9" action={interaction} onClose={vi.fn()} />);

  expect(screen.getByText(/Registrar llamada de Ana López/)).toBeTruthy();
  fireEvent.click(screen.getByText("Confirmar"));
  await waitFor(() => expect(chatStream.resumeChat).toHaveBeenCalledWith("t9", "confirm"));
  await waitFor(() => expect(screen.getByText(/Interacción registrada/)).toBeTruthy());
});
```

- [ ] **Step 2: Run the frontend tests**

Run: `npm --prefix frontend run test -- --run` (or `cd frontend; npx vitest run`)
Expected: PASS (existing + the new contract test).

- [ ] **Step 3: Commit**

```bash
git add frontend/components/ConfirmCard.test.tsx
git commit -m "test(front): ConfirmCard renders a log_interaction action (contract)"
```

---

## Task 10: whole-branch verification + smoke

**No new code.** Gate the branch before review/merge.

- [ ] **Step 1: Lint + format**

Run: `backend\.venv\Scripts\ruff check backend` then `backend\.venv\Scripts\ruff format backend`
Expected: `All checks passed!` / files unchanged (or formatted then re-commit).

- [ ] **Step 2: Types**

Run: `backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml`
Expected: `Success: no issues found`.

- [ ] **Step 3: Full no-llm suite**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (≥ 130 + new tests; ~+18).

- [ ] **Step 4: e2e (`-m llm`)** — Ollama + Postgres + seed up:

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m llm -q`
Expected: PASS.

- [ ] **Step 5: Frontend** (if Task 9 done)

Run: `npm --prefix frontend run lint; npm --prefix frontend run build; npx --prefix frontend vitest run`
Expected: green.

- [ ] **Step 6: Browser smoke (CLAUDE.md §2)** — start infra/backend/front, then:
  1. *"registrá que llamé a \<cliente del seed\> y confirmó el turno"* → **confirmation card opens** → **Confirmar** → "✅ Interacción registrada…" → verify in DB: `SELECT count(*) FROM interactions` increased by 1 for that client.
  2. **Cancelar** on a fresh interaction → no row written.
  3. *"agendá un turno para \<cliente\> con \<profesional\> mañana 10"* → card opens → Confirmar writes to `appointments` (**no-regression** of the 1st tool).
  4. *"cancelá el turno de Juan"* → **unsupported** → cordial abstention, no card, no write.

- [ ] **Step 7: Update the session handoff docs** — append a "Cierre Slice 5" section to `docs/NEXT_SESSION.md` (mirror the Slice 4 entry: what merged, gate counts, smoke result, decisions) and the `## PROMPT` slice list. Commit:

```bash
git add docs/NEXT_SESSION.md
git commit -m "docs(next-session): cierre Slice 5 log_interaction + write-tool registry"
```

> Merge to `main` (`--no-ff`, matching prior slices) is handled by the **finishing-a-development-branch** skill after a whole-branch code review (requesting-code-review). Not a task step here.

---

## Self-Review (done while writing)

**Spec coverage:** registry + generic dispatch → T5/T6; classifier + `unsupported` → T5/T6; `interactions` table §5.2 → T1; `log_interaction` writer with tenant guard → T2; shared client resolver → T3; `ProposedInteraction` (summary+content one call, default `nota`, occurred_at=now, source=agente) → T4; router/transport/front unchanged → T6 (router untouched) / no transport task / T9 (optional); no-regression of appointments → T6 (appointment tests kept) + T7 (parametrized) + T8 (e2e kept); tests across all layers → T1–T9; DoD gates → T10. No gaps found.

**Placeholder scan:** every code step has complete code; every run step has an exact command + expected result. No TBD/TODO.

**Type/name consistency:** `WriteTool(kind, propose, write, format_receipt, cancel_message)` used identically in T5 (def), T6 (test fakes), T7 (test fakes). `proposed_action = {kind, summary, params}` consistent T4/T6/T7. `classify_write_action(question, llm=None) -> str` consistent T5/T6/T7. `resolve_single_client(...) -> ClientResolution(client, abstain_message, abstain_reason)` consistent T3/T4. `db.log_interaction(practice_id, client_id, *, type, summary, content, occurred_at, source)` consistent T2/T5.

**Deviation from spec (noted):** the spec's file-map said `action_agent.py` would adopt `resolve_single_client`; to keep the appointment path **behavior-identical** (lower regression risk), `action_agent.py` is left **unchanged** and the shared resolver is used only by `interaction_agent` this slice. Unifying the appointment resolver onto the shared helper is a trivial, separate follow-up.

**Deviation #2 (during execution, user-approved 2026-06-28):** Task 8's `-m llm` e2e surfaced that the e4b router's `with_structured_output(RouterDecision)` returns `None` **intermittently** for the "registrá…" action phrases → the original `classify_intent` (`return decision.intent`) would crash intermittently (`AttributeError`), making the slice's own e2e flaky. With the user's approval, Task 8 also switched `graph/router.py` `classify_intent` to **text-parse** (`ainvoke` + exact-then-substring match, retry-once, safe `chitchat` fallback) and adapted `tests/test_router.py` fakes — matching the CLAUDE.md structured-output gotcha pattern already used by the SQL agent. The router's coarse **role** is unchanged (only its decode mechanism). Files touched beyond Task 8's list: `backend/app/graph/router.py`, `backend/tests/test_router.py`. See the spec Addendum.
