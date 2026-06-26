# Data Agent NL2SQL (lectura read-only) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar `sql_stub` por un Data Agent read-only que traduce preguntas en español a un `SELECT` validado, lo ejecuta contra Postgres y responde en el chat (p. ej. *"¿cuántos turnos esta semana?"* → "Tenés 12 turnos esta semana.").

**Architecture:** Approach A (CLAUDE.md §4): `gemma4:12b` genera el `SELECT` usando la capa semántica + esquema introspectado como contexto → validación dura con `sqlglot` (solo SELECT, allow-list, `practice_id`, LIMIT) → juez intención↔SQL en `gemma4:e4b` → retry cap 2 → ejecución en transacción `READ ONLY` → síntesis grounded en `gemma4:12b`. El Data Agent es una función pura de negocio (`agents/sql_agent.py`); el `sql_node` del grafo sintetiza y emite el SSE (mismo patrón que el subgrafo CRAG + `rag_node`).

**Tech Stack:** Python 3.12, FastAPI, asyncpg, LangGraph, `langchain-ollama` (ChatOllama local), `sqlglot` (validación SQL), `PyYAML` (capa semántica), `Faker` (seed sintético), pytest + pytest-asyncio (`asyncio_mode=auto`).

## Global Constraints

- **Inferencia 100% local por Ollama**: `ChatOllama` vía `app.llm.make_llm`; nunca APIs externas. Generación/síntesis con `gemma4:12b` (`settings.ollama_model`); juez con `gemma4:e4b`.
- **Costo $0 / solo OSS**: deps nuevas `sqlglot`, `PyYAML`, `Faker` (todas pip/OSS). Sin servicios cloud.
- **Solo lectura este slice**: únicamente `SELECT`. `action_stub` queda **intacto** (las escrituras siguen pidiendo confirmación en su slice).
- **Aislamiento multi-tenant**: toda query filtra por `practice_id` (CLAUDE.md §0.5). El validador exige el predicado; el ejecutor corre `READ ONLY`. RLS real = Fase 4.
- **Salida estructurada obligatoria**: tool calls / SQL / veredictos por `with_structured_output` (Pydantic). Prohibido parsear JSON con regex (CLAUDE.md §4).
- **PII fuera de logs**: nunca loguear filas crudas (pueden traer nombres de pacientes); loguear `sql` + conteo de filas + veredicto.
- **Gates**: `ruff check . && ruff format .`; `mypy` **siempre con `--config-file backend/pyproject.toml`** (sin eso da falso-positivo `asyncpg [import-untyped]`); `pytest -m "not llm"` verde (asume Postgres/Qdrant arriba por `docker compose up -d`).
- **Commits limpios**: en español, conventional commits; **sin ninguna atribución a Claude/Anthropic** (CLAUDE.md §6 — innegociable).
- **Comandos (Windows / PowerShell)**: Python del venv = `backend\.venv\Scripts\python`. PowerShell NO soporta `cd x && y`.
- **Rama de trabajo**: `fase-1/nl2sql-data-agent` (ya creada; el spec ya está commiteado ahí).

Valores de config (Task 1): `sql_row_limit=200`, `sql_timeout_ms=5000`, `sql_max_attempts=2`.

---

### Task 1: Fundaciones — deps, tabla `appointments`, config

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/schema.sql` (append tabla `appointments`)
- Modify: `backend/app/config.py:18-25` (nuevos campos en `Settings`)
- Test: `backend/tests/test_config.py` (append), `backend/tests/test_schema.py` (create)

**Interfaces:**
- Produces: `Settings.sql_row_limit: int = 200`, `Settings.sql_timeout_ms: int = 5000`, `Settings.sql_max_attempts: int = 2`. Tabla `appointments` (Blueprint §5.2). Deps `sqlglot`, `PyYAML`, `Faker` instalables.

- [ ] **Step 1: Agregar dependencias**

En `backend/requirements.txt`, agregar al final:
```
sqlglot==25.*
PyYAML==6.*
Faker==30.*
```

- [ ] **Step 2: Instalar deps**

Run: `backend\.venv\Scripts\python -m pip install "sqlglot==25.*" "PyYAML==6.*" "Faker==30.*"`
Expected: instala sin error; `Successfully installed ...`.

- [ ] **Step 3: Agregar la tabla `appointments` al esquema**

Append a `backend/app/schema.sql` (idempotente, convención del archivo):
```sql

-- ====== Turnos / citas ======
CREATE TABLE IF NOT EXISTS appointments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id     UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    practitioner_id UUID NOT NULL REFERENCES practitioners(id),
    start_at        TIMESTAMPTZ NOT NULL,
    end_at          TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'programado'
                    CHECK (status IN ('programado','confirmado','atendido','ausente','cancelado')),
    reason          TEXT,
    channel         TEXT,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_appt_practice_date ON appointments(practice_id, start_at);
CREATE INDEX IF NOT EXISTS idx_appt_client ON appointments(client_id);
```

- [ ] **Step 4: Aplicar el esquema a la DB**

Run: `docker compose exec -T postgres psql -U praxia -d praxia < backend/app/schema.sql`
Expected: `CREATE TABLE` / `CREATE INDEX` (o `NOTICE ... already exists`, idempotente).

- [ ] **Step 5: Escribir el test de config (falla)**

Append a `backend/tests/test_config.py`:
```python
def test_settings_have_sql_defaults() -> None:
    get_settings.cache_clear()
    s = get_settings()
    assert s.sql_row_limit == 200
    assert s.sql_timeout_ms == 5000
    assert s.sql_max_attempts == 2
```

- [ ] **Step 6: Crear el test de esquema (falla)**

Create `backend/tests/test_schema.py`:
```python
import pytest

from app import db


@pytest.mark.integration
async def test_appointments_table_has_expected_columns() -> None:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'appointments'"
    )
    cols = {r["column_name"] for r in rows}
    assert {
        "practice_id", "client_id", "practitioner_id", "start_at", "end_at", "status",
    } <= cols
```

- [ ] **Step 7: Correr ambos tests (fallan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_config.py::test_settings_have_sql_defaults backend/tests/test_schema.py -q`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'sql_row_limit'`; el de esquema pasa si el Step 4 corrió, pero el de config falla).

- [ ] **Step 8: Agregar los campos a `Settings`**

En `backend/app/config.py`, después de `rag_max_attempts: int = 2` (línea ~25):
```python
    sql_row_limit: int = 200
    sql_timeout_ms: int = 5000
    sql_max_attempts: int = 2
```

- [ ] **Step 9: Correr los tests (pasan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_config.py backend/tests/test_schema.py -q`
Expected: PASS.

- [ ] **Step 10: Lint + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format .`
```bash
git add backend/requirements.txt backend/app/schema.sql backend/app/config.py backend/tests/test_config.py backend/tests/test_schema.py
git commit -m "feat(nl2sql): tabla appointments, config SQL y deps (sqlglot/pyyaml/faker)"
```

---

### Task 2: Seeder sintético (`seed_demo.py`)

**Files:**
- Create: `backend/seed_demo.py`
- Test: `backend/tests/test_seed_demo.py`

**Interfaces:**
- Consumes: tabla `appointments` (Task 1), `app.db.get_pool`, `settings.practice_id`, `Faker`.
- Produces: `async def seed_demo() -> dict[str, int]` (claves `practitioners`, `clients`, `appointments`). Idempotente. Garantiza ≥12 turnos en la semana actual.

- [ ] **Step 1: Escribir el test (falla)**

Create `backend/tests/test_seed_demo.py`:
```python
import pytest

from app import db


@pytest.mark.integration
async def test_seed_demo_creates_data_with_appointments_this_week() -> None:
    from seed_demo import seed_demo

    counts = await seed_demo()
    assert counts["practitioners"] >= 3
    assert counts["clients"] >= 30
    assert counts["appointments"] >= 12

    pool = await db.get_pool()
    n_week = await pool.fetchval(
        "SELECT count(*) FROM appointments "
        "WHERE start_at >= date_trunc('week', now()) "
        "AND start_at < date_trunc('week', now()) + interval '7 days'"
    )
    assert n_week >= 12

    n_active = await pool.fetchval("SELECT count(*) FROM clients WHERE status = 'activo'")
    assert n_active >= 1
```

- [ ] **Step 2: Correr el test (falla)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_seed_demo.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'seed_demo'`).

- [ ] **Step 3: Implementar el seeder**

Create `backend/seed_demo.py`:
```python
"""Seeder de datos sintéticos para el demo (CLAUDE.md §7: Faker, nada real).

Idempotente y determinístico (semilla fija). Las fechas de los turnos son
relativas a now() para que "esta semana" siempre tenga datos.

Uso: backend\\.venv\\Scripts\\python backend\\seed_demo.py
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from faker import Faker

from app import db
from app.config import get_settings

_NS = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_APPT_STATUS = ["programado", "confirmado", "atendido", "ausente", "cancelado"]
_CLIENT_STATUS = ["activo", "activo", "activo", "inactivo", "baja"]  # sesgo a activo


def _det_uuid(label: str) -> str:
    return str(uuid.uuid5(_NS, label))


async def seed_demo() -> dict[str, int]:
    settings = get_settings()
    practice_id = settings.practice_id
    fake = Faker("es_AR")
    fake.seed_instance(42)
    rng = random.Random(42)
    pool = await db.get_pool()

    practitioners: list[str] = []
    for i in range(3):
        pid = _det_uuid(f"prac-{i}")
        practitioners.append(pid)
        await pool.execute(
            "INSERT INTO practitioners (id, practice_id, full_name, speciality, active) "
            "VALUES ($1, $2, $3, $4, true) ON CONFLICT (id) DO NOTHING",
            pid, practice_id, fake.name(),
            rng.choice(["Clínica", "Psicología", "Odontología"]),
        )

    clients: list[str] = []
    for i in range(30):
        cid = _det_uuid(f"client-{i}")
        clients.append(cid)
        await pool.execute(
            "INSERT INTO clients (id, practice_id, full_name, status) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
            cid, practice_id, fake.name(), rng.choice(_CLIENT_STATUS),
        )

    # appointments: borrar y reinsertar (no referenciadas por otras tablas de este schema)
    await pool.execute("DELETE FROM appointments WHERE practice_id = $1", practice_id)
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    starts: list[datetime] = []
    for _ in range(12):  # garantizados esta semana
        starts.append(monday + timedelta(days=rng.randint(0, 6), hours=rng.randint(8, 18)))
    for _ in range(68):  # dispersos en el pasado/futuro
        base = now + timedelta(days=rng.randint(-45, 20))
        starts.append(base.replace(hour=rng.randint(8, 18), minute=0, second=0, microsecond=0))

    for i, start in enumerate(starts):
        end = start + timedelta(minutes=rng.choice([30, 45]))
        await pool.execute(
            "INSERT INTO appointments "
            "(id, practice_id, client_id, practitioner_id, start_at, end_at, status, reason, channel) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            _det_uuid(f"appt-{i}"), practice_id, rng.choice(clients), rng.choice(practitioners),
            start, end, rng.choice(_APPT_STATUS), fake.sentence(nb_words=4),
            rng.choice(["presencial", "telellamada"]),
        )

    return {
        "practitioners": len(practitioners),
        "clients": len(clients),
        "appointments": len(starts),
    }


if __name__ == "__main__":
    print(f"seed_demo: {asyncio.run(seed_demo())}")
```

- [ ] **Step 4: Correr el test (pasa)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_seed_demo.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format .`
```bash
git add backend/seed_demo.py backend/tests/test_seed_demo.py
git commit -m "feat(nl2sql): seeder sintético de turnos/clientes (Faker, determinístico)"
```

---

### Task 3: Capa semántica (`model.yaml` + `resolver.py`)

**Files:**
- Create: `backend/app/semantic_layer/__init__.py` (vacío)
- Create: `backend/app/semantic_layer/model.yaml`
- Create: `backend/app/semantic_layer/resolver.py`
- Test: `backend/tests/test_semantic_layer.py`

**Interfaces:**
- Consumes: `app.db.get_pool` (introspección), `PyYAML`.
- Produces:
  - `class SemanticLayer` (frozen dataclass): `schema_context: str`, `semantic_context: str`, `allowed_tables: frozenset[str]`, `allowed_columns: dict[str, frozenset[str]]`.
  - `parse_model_yaml(path: str | None = None) -> dict`
  - `allowed_tables_from(spec: dict) -> frozenset[str]`  → `{"appointments","clients","practitioners"}`
  - `render_semantic_context(spec: dict) -> str`
  - `async def load_semantic_layer(pool: Any = None) -> SemanticLayer` (memoizada)

- [ ] **Step 1: Crear el `model.yaml`**

Create `backend/app/semantic_layer/model.yaml`:
```yaml
entities:
  appointments: { table: appointments, time_dimension: start_at }
  clients:      { table: clients,      time_dimension: created_at }

metrics:
  turnos_totales:
    sql: "COUNT(*)"
    from: appointments
    synonyms: ["turnos", "citas"]
  ausencias:
    sql: "COUNT(*) FILTER (WHERE status = 'ausente')"
    from: appointments
    synonyms: ["ausentes", "no shows", "faltas", "inasistencias"]
  clientes_activos:
    sql: "COUNT(*) FILTER (WHERE status = 'activo')"
    from: clients
    synonyms: ["pacientes activos", "clientes activos"]

dimensions:
  por_profesional:
    sql: "practitioners.full_name"
    join: "JOIN practitioners ON appointments.practitioner_id = practitioners.id"
  por_semana: { sql: "date_trunc('week', start_at)" }
  por_mes:    { sql: "date_trunc('month', start_at)" }
  por_estado: { sql: "status" }

glossary:
  paciente: clients
  pacientes: clients
  cliente: clients
  turno: appointments
  cita: appointments
```

- [ ] **Step 2: Crear `__init__.py` vacío**

Create `backend/app/semantic_layer/__init__.py` con contenido vacío.

- [ ] **Step 3: Escribir los tests puros (fallan)**

Create `backend/tests/test_semantic_layer.py`:
```python
import pytest

from app.semantic_layer import resolver


def test_parse_model_yaml_has_expected_shape() -> None:
    spec = resolver.parse_model_yaml()
    assert "appointments" in spec["entities"]
    assert "turnos_totales" in spec["metrics"]
    assert "por_profesional" in spec["dimensions"]
    assert spec["glossary"]["paciente"] == "clients"


def test_allowed_tables_includes_entities_and_joined_tables() -> None:
    spec = resolver.parse_model_yaml()
    tables = resolver.allowed_tables_from(spec)
    assert tables == frozenset({"appointments", "clients", "practitioners"})


def test_render_semantic_context_mentions_metrics_and_glossary() -> None:
    spec = resolver.parse_model_yaml()
    ctx = resolver.render_semantic_context(spec)
    assert "turnos_totales" in ctx
    assert "ausencias" in ctx
    assert "paciente" in ctx


@pytest.mark.integration
async def test_load_semantic_layer_introspects_columns() -> None:
    layer = await resolver.load_semantic_layer()
    assert "appointments" in layer.allowed_columns
    assert "practice_id" in layer.allowed_columns["appointments"]
    assert "start_at" in layer.allowed_columns["appointments"]
    assert "appointments(" in layer.schema_context
```

- [ ] **Step 4: Correr los tests (fallan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_semantic_layer.py -q`
Expected: FAIL (`AttributeError: module 'app.semantic_layer.resolver' has no attribute 'parse_model_yaml'`).

- [ ] **Step 5: Implementar el resolver**

Create `backend/app/semantic_layer/resolver.py`:
```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app import db

_MODEL_PATH = Path(__file__).with_name("model.yaml")


@dataclass(frozen=True)
class SemanticLayer:
    schema_context: str
    semantic_context: str
    allowed_tables: frozenset[str]
    allowed_columns: dict[str, frozenset[str]]


_cache: SemanticLayer | None = None


def parse_model_yaml(path: str | None = None) -> dict[str, Any]:
    text = Path(path or _MODEL_PATH).read_text(encoding="utf-8")
    spec = yaml.safe_load(text)
    for key in ("entities", "metrics", "dimensions", "glossary"):
        spec.setdefault(key, {})
    return spec


def allowed_tables_from(spec: dict[str, Any]) -> frozenset[str]:
    tables: set[str] = {e["table"] for e in spec["entities"].values()}
    for dim in spec["dimensions"].values():
        join = dim.get("join", "")
        for token in join.replace("JOIN", " ").split():
            if token in {"appointments", "clients", "practitioners", "interactions", "invoices"}:
                tables.add(token)
    return frozenset(tables)


def render_semantic_context(spec: dict[str, Any]) -> str:
    lines: list[str] = ["Métricas:"]
    for name, m in spec["metrics"].items():
        syn = f" (sinónimos: {', '.join(m['synonyms'])})" if m.get("synonyms") else ""
        lines.append(f"- {name}: {m['sql']} sobre {m['from']}{syn}")
    lines.append("Dimensiones:")
    for name, d in spec["dimensions"].items():
        join = f" [{d['join']}]" if d.get("join") else ""
        lines.append(f"- {name}: {d['sql']}{join}")
    lines.append("Glosario:")
    for term, table in spec["glossary"].items():
        lines.append(f"- {term} → {table}")
    return "\n".join(lines)


async def introspect_columns(pool: Any, tables: frozenset[str]) -> dict[str, frozenset[str]]:
    rows = await pool.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
        "ORDER BY table_name, ordinal_position",
        list(tables),
    )
    acc: dict[str, set[str]] = {}
    for r in rows:
        acc.setdefault(r["table_name"], set()).add(r["column_name"])
    return {t: frozenset(cols) for t, cols in acc.items()}


def _render_schema(columns: dict[str, frozenset[str]]) -> str:
    return "\n".join(f"{t}({', '.join(sorted(columns[t]))})" for t in sorted(columns))


async def load_semantic_layer(pool: Any = None) -> SemanticLayer:
    global _cache
    if _cache is not None:
        return _cache
    spec = parse_model_yaml()
    tables = allowed_tables_from(spec)
    pool = pool or await db.get_pool()
    columns = await introspect_columns(pool, tables)
    _cache = SemanticLayer(
        schema_context=_render_schema(columns),
        semantic_context=render_semantic_context(spec),
        allowed_tables=tables,
        allowed_columns=columns,
    )
    return _cache
```

- [ ] **Step 6: Correr los tests (pasan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_semantic_layer.py -q`
Expected: PASS.

- [ ] **Step 7: Lint + typecheck + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format . ; backend\.venv\Scripts\python -m mypy app/ --config-file backend/pyproject.toml`
Expected: ruff clean; mypy `Success`.
```bash
git add backend/app/semantic_layer/ backend/tests/test_semantic_layer.py
git commit -m "feat(nl2sql): capa semántica (model.yaml + resolver con introspección)"
```

---

### Task 4: Validador SQL (`agents/sql_agent.py` — validate_select)

**Files:**
- Create: `backend/app/agents/__init__.py` (vacío)
- Create: `backend/app/agents/sql_agent.py` (dataclasses + validador)
- Test: `backend/tests/test_sql_validator.py`

**Interfaces:**
- Consumes: `sqlglot`.
- Produces:
  - `@dataclass class ValidationResult: ok: bool; sql: str; reason: str`
  - `@dataclass class SqlResult: sql: str | None; rows: list[dict]; columns: list[str]; abstained: bool; reason: str`
  - `class SqlDraft(BaseModel): sql: str`
  - `class SqlIntentVerdict(BaseModel): matches: bool; reason: str`
  - `validate_select(sql: str, allowed_tables: frozenset[str], practice_id: str, row_limit: int) -> ValidationResult` (sql = normalizado con LIMIT cuando ok)

- [ ] **Step 1: Crear `__init__.py` vacío**

Create `backend/app/agents/__init__.py` con contenido vacío.

- [ ] **Step 2: Escribir los tests del validador (fallan)**

Create `backend/tests/test_sql_validator.py`:
```python
from app.agents.sql_agent import validate_select

PID = "00000000-0000-0000-0000-000000000001"
ALLOWED = frozenset({"appointments", "clients", "practitioners"})


def _v(sql: str):
    return validate_select(sql, ALLOWED, PID, row_limit=200)


def test_accepts_select_with_practice_filter() -> None:
    r = _v(f"SELECT count(*) FROM appointments WHERE practice_id = '{PID}'")
    assert r.ok
    assert "LIMIT" in r.sql.upper()


def test_rejects_insert() -> None:
    assert not _v(f"INSERT INTO clients (practice_id) VALUES ('{PID}')").ok


def test_rejects_update() -> None:
    assert not _v(f"UPDATE clients SET status='baja' WHERE practice_id='{PID}'").ok


def test_rejects_delete() -> None:
    assert not _v(f"DELETE FROM clients WHERE practice_id='{PID}'").ok


def test_rejects_multiple_statements() -> None:
    assert not _v(f"SELECT 1 FROM clients WHERE practice_id='{PID}'; DROP TABLE clients").ok


def test_rejects_table_outside_allowlist() -> None:
    assert not _v(f"SELECT * FROM invoices WHERE practice_id = '{PID}'").ok


def test_rejects_missing_practice_filter() -> None:
    assert not _v("SELECT count(*) FROM appointments").ok


def test_injects_limit_when_missing() -> None:
    r = _v(f"SELECT full_name FROM clients WHERE practice_id = '{PID}'")
    assert r.ok and "LIMIT 200" in r.sql.upper().replace("  ", " ")


def test_clamps_limit_over_cap() -> None:
    r = _v(f"SELECT full_name FROM clients WHERE practice_id = '{PID}' LIMIT 9999")
    assert r.ok and "9999" not in r.sql
```

- [ ] **Step 3: Correr los tests (fallan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_validator.py -q`
Expected: FAIL (`ModuleNotFoundError` / `ImportError: cannot import name 'validate_select'`).

- [ ] **Step 4: Implementar dataclasses + validador**

Create `backend/app/agents/sql_agent.py`:
```python
from dataclasses import dataclass, field

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter, exp.Command,
)


@dataclass
class ValidationResult:
    ok: bool
    sql: str
    reason: str


@dataclass
class SqlResult:
    sql: str | None
    rows: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    abstained: bool = False
    reason: str = ""


class SqlDraft(BaseModel):
    sql: str


class SqlIntentVerdict(BaseModel):
    matches: bool
    reason: str


def _has_practice_filter(root: exp.Expression, practice_id: str) -> bool:
    for eq in root.find_all(exp.EQ):
        col = eq.find(exp.Column)
        lit = eq.find(exp.Literal)
        if col is not None and col.name == "practice_id" and lit is not None:
            if lit.is_string and lit.name == practice_id:
                return True
    return False


def _clamp_limit(select: exp.Select, cap: int) -> None:
    limit = select.args.get("limit")
    if limit is None:
        select.limit(cap, copy=False)
        return
    try:
        value = int(limit.expression.name)
    except (AttributeError, ValueError):
        select.limit(cap, copy=False)
        return
    if value > cap:
        select.limit(cap, copy=False)


def validate_select(
    sql: str, allowed_tables: frozenset[str], practice_id: str, row_limit: int
) -> ValidationResult:
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as e:  # noqa: BLE001 - parse error de sqlglot
        return ValidationResult(False, sql, f"parse_error: {e}")
    if len(statements) != 1:
        return ValidationResult(False, sql, "no es una sola sentencia")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.With)):
        return ValidationResult(False, sql, "no es un SELECT")
    main = root if isinstance(root, exp.Select) else root.this
    if not isinstance(main, exp.Select):
        return ValidationResult(False, sql, "WITH no envuelve un SELECT")
    for forbidden in _FORBIDDEN:
        if root.find(forbidden) is not None:
            return ValidationResult(False, sql, f"contiene {forbidden.__name__}")
    tables = {t.name for t in root.find_all(exp.Table)}
    extra = tables - allowed_tables
    if extra:
        return ValidationResult(False, sql, f"tablas fuera de allow-list: {sorted(extra)}")
    if not _has_practice_filter(root, practice_id):
        return ValidationResult(False, sql, "falta filtro practice_id")
    _clamp_limit(main, row_limit)
    return ValidationResult(True, root.sql(dialect="postgres"), "ok")
```

- [ ] **Step 5: Correr los tests (pasan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_validator.py -q`
Expected: PASS (9 passed).

- [ ] **Step 6: Lint + typecheck + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format . ; backend\.venv\Scripts\python -m mypy app/ --config-file backend/pyproject.toml`
Expected: ruff clean; mypy `Success`.
```bash
git add backend/app/agents/__init__.py backend/app/agents/sql_agent.py backend/tests/test_sql_validator.py
git commit -m "feat(nl2sql): validador de SELECT con sqlglot (allow-list, practice_id, LIMIT)"
```

---

### Task 5: Ejecutor read-only (`db.run_select`)

**Files:**
- Modify: `backend/app/db.py` (append función)
- Test: `backend/tests/test_run_select.py`

**Interfaces:**
- Consumes: `app.db.get_pool`.
- Produces: `async def run_select(sql: str, *, timeout_ms: int, row_limit: int) -> tuple[list[dict], list[str]]`. Corre en transacción `READ ONLY` con `statement_timeout`; recorta a `row_limit`.

- [ ] **Step 1: Escribir los tests (fallan)**

Create `backend/tests/test_run_select.py`:
```python
import pytest

from app import db


@pytest.mark.integration
async def test_run_select_returns_rows_and_columns() -> None:
    rows, columns = await db.run_select(
        "SELECT 1 AS uno, 2 AS dos", timeout_ms=5000, row_limit=200
    )
    assert rows == [{"uno": 1, "dos": 2}]
    assert columns == ["uno", "dos"]


@pytest.mark.integration
async def test_run_select_blocks_writes() -> None:
    with pytest.raises(Exception):  # noqa: B017 - asyncpg ReadOnlySqlTransactionError
        await db.run_select(
            "INSERT INTO clients (practice_id, full_name) "
            "VALUES ('00000000-0000-0000-0000-000000000001', 'x')",
            timeout_ms=5000,
            row_limit=200,
        )


@pytest.mark.integration
async def test_run_select_respects_row_limit() -> None:
    rows, _ = await db.run_select(
        "SELECT * FROM generate_series(1, 50) AS g(n)", timeout_ms=5000, row_limit=10
    )
    assert len(rows) == 10
```

- [ ] **Step 2: Correr los tests (fallan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_run_select.py -q`
Expected: FAIL (`AttributeError: module 'app.db' has no attribute 'run_select'`).

- [ ] **Step 3: Implementar `run_select`**

Append a `backend/app/db.py`:
```python
async def run_select(
    sql: str, *, timeout_ms: int, row_limit: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Ejecuta un SELECT ya validado en una transacción READ ONLY.

    Defensa en profundidad: aunque la validación fallara, la transacción no
    puede escribir. `statement_timeout` corta queries lentas; las filas se
    recortan a `row_limit`.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            records = await conn.fetch(sql)
    rows = [dict(r) for r in records[:row_limit]]
    columns = list(rows[0].keys()) if rows else []
    return rows, columns
```

- [ ] **Step 4: Correr los tests (pasan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_run_select.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint + typecheck + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format . ; backend\.venv\Scripts\python -m mypy app/ --config-file backend/pyproject.toml`
Expected: ruff clean; mypy `Success`.
```bash
git add backend/app/db.py backend/tests/test_run_select.py
git commit -m "feat(nl2sql): ejecutor run_select read-only (transacción READ ONLY + timeout + tope)"
```

---

### Task 6: Pipeline del Data Agent (`answer_structured`)

**Files:**
- Modify: `backend/app/agents/sql_agent.py` (append pipeline + prompts)
- Test: `backend/tests/test_sql_agent.py`

**Interfaces:**
- Consumes: `validate_select`, `SqlResult`, `SqlDraft`, `SqlIntentVerdict` (Task 4); `SemanticLayer`/`load_semantic_layer` (Task 3); `run_select` (Task 5); `make_llm`; `get_settings`.
- Produces: `async def answer_structured(question: str, practice_id: str, *, pool: Any = None, gen_llm: Any = None, judge_llm: Any = None) -> SqlResult`.

- [ ] **Step 1: Escribir los tests (fallan)**

Create `backend/tests/test_sql_agent.py`:
```python
from app.agents import sql_agent
from app.agents.sql_agent import SqlDraft, SqlIntentVerdict
from app.semantic_layer.resolver import SemanticLayer

PID = "00000000-0000-0000-0000-000000000001"
GOOD_SQL = f"SELECT count(*) AS total FROM appointments WHERE practice_id = '{PID}'"
LAYER = SemanticLayer(
    schema_context="appointments(practice_id, start_at, status)",
    semantic_context="Métricas: turnos_totales",
    allowed_tables=frozenset({"appointments", "clients", "practitioners"}),
    allowed_columns={"appointments": frozenset({"practice_id", "start_at", "status"})},
)


class _FakeStructured:
    def __init__(self, results: list) -> None:
        self._results = results
        self._i = 0

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r


class FakeLLM:
    def __init__(self, *results) -> None:  # type: ignore[no-untyped-def]
        self._results = list(results)

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._results)


def _patch_common(monkeypatch, rows=None):  # type: ignore[no-untyped-def]
    async def _fake_loader(pool=None):  # type: ignore[no-untyped-def]
        return LAYER

    async def _fake_run_select(sql, *, timeout_ms, row_limit):  # type: ignore[no-untyped-def]
        return (rows if rows is not None else [{"total": 12}]), ["total"]

    monkeypatch.setattr(sql_agent, "load_semantic_layer", _fake_loader)
    monkeypatch.setattr(sql_agent, "run_select", _fake_run_select)


async def test_happy_path_returns_rows(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "¿cuántos turnos?", PID,
        gen_llm=FakeLLM(SqlDraft(sql=GOOD_SQL)),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=True, reason="ok")),
    )
    assert not result.abstained
    assert result.rows == [{"total": 12}]
    assert result.sql and "appointments" in result.sql


async def test_retries_after_invalid_sql(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "¿cuántos turnos?", PID,
        gen_llm=FakeLLM(SqlDraft(sql="INSERT INTO clients DEFAULT VALUES"), SqlDraft(sql=GOOD_SQL)),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=True, reason="ok")),
    )
    assert not result.abstained
    assert result.rows == [{"total": 12}]


async def test_abstains_after_cap(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "algo", PID,
        gen_llm=FakeLLM(SqlDraft(sql="INSERT INTO clients DEFAULT VALUES")),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=True, reason="ok")),
    )
    assert result.abstained
    assert result.sql is None


async def test_abstains_when_judge_rejects(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "algo", PID,
        gen_llm=FakeLLM(SqlDraft(sql=GOOD_SQL)),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=False, reason="no responde")),
    )
    assert result.abstained
```

- [ ] **Step 2: Correr los tests (fallan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_agent.py -q`
Expected: FAIL (`AttributeError: module 'app.agents.sql_agent' has no attribute 'answer_structured'`).

- [ ] **Step 3: Implementar el pipeline**

Agregar a los imports de `backend/app/agents/sql_agent.py` (arriba del archivo, después de `from sqlglot import exp`):
```python
from typing import Any

from app.config import get_settings
from app.db import run_select
from app.llm import make_llm
from app.semantic_layer.resolver import SemanticLayer, load_semantic_layer
```

Append al final de `backend/app/agents/sql_agent.py`:
```python
def _gen_messages(
    question: str, layer: SemanticLayer, practice_id: str, feedback: str
) -> list[tuple[str, str]]:
    system = (
        "Sos un generador de SQL para PostgreSQL de un CRM de prácticas profesionales. "
        "Generá UNA sola consulta SELECT de solo lectura (sin comentarios, sin punto y coma, "
        "sin múltiples sentencias). Usá EXCLUSIVAMENTE estas tablas y columnas:\n"
        f"{layer.schema_context}\n\n"
        "Métricas, dimensiones y sinónimos de negocio (guía):\n"
        f"{layer.semantic_context}\n\n"
        f"OBLIGATORIO: filtrá siempre por practice_id = '{practice_id}'. "
        "Para fechas relativas usá now() y date_trunc (esta semana = "
        "start_at >= date_trunc('week', now()) AND "
        "start_at < date_trunc('week', now()) + interval '7 days'). "
        "Respondé solo el SELECT."
    )
    human = f"Pregunta: {question}"
    if feedback:
        human += f"\n\nIntento anterior fallido: {feedback}"
    return [("system", system), ("human", human)]


def _judge_messages(question: str, sql: str) -> list[tuple[str, str]]:
    system = (
        "Sos un juez. Decidí si la consulta SQL responde la intención de la pregunta. "
        "matches=true solo si el SELECT devuelve exactamente lo que se pidió."
    )
    return [("system", system), ("human", f"Pregunta: {question}\n\nSQL:\n{sql}")]


async def answer_structured(
    question: str,
    practice_id: str,
    *,
    pool: Any = None,
    gen_llm: Any = None,
    judge_llm: Any = None,
) -> SqlResult:
    settings = get_settings()
    layer = await load_semantic_layer(pool)
    gen = (gen_llm or make_llm(settings.ollama_model, temperature=0.0)).with_structured_output(
        SqlDraft
    )
    judge = (judge_llm or make_llm("gemma4:e4b", temperature=0.0)).with_structured_output(
        SqlIntentVerdict
    )
    feedback = ""
    last_reason = "sin intentos"
    for _ in range(settings.sql_max_attempts):
        try:
            draft: SqlDraft = await gen.ainvoke(
                _gen_messages(question, layer, practice_id, feedback)
            )
            vr = validate_select(
                draft.sql, layer.allowed_tables, practice_id, settings.sql_row_limit
            )
            if not vr.ok:
                last_reason = vr.reason
                feedback = f"SQL inválido: {vr.reason}. Corregilo."
                continue
            verdict: SqlIntentVerdict = await judge.ainvoke(_judge_messages(question, vr.sql))
            if not verdict.matches:
                last_reason = verdict.reason
                feedback = f"El SQL no respondía la intención: {verdict.reason}."
                continue
            rows, columns = await run_select(
                vr.sql, timeout_ms=settings.sql_timeout_ms, row_limit=settings.sql_row_limit
            )
            return SqlResult(sql=vr.sql, rows=rows, columns=columns, abstained=False, reason="ok")
        except Exception as e:  # noqa: BLE001 - fail-closed: cualquier fallo cuenta como intento
            last_reason = f"error: {e}"
            feedback = "Reintentá con un SELECT simple y válido."
    return SqlResult(sql=None, abstained=True, reason=last_reason)
```

- [ ] **Step 4: Correr los tests (pasan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_agent.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + typecheck + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format . ; backend\.venv\Scripts\python -m mypy app/ --config-file backend/pyproject.toml`
Expected: ruff clean; mypy `Success`.
```bash
git add backend/app/agents/sql_agent.py backend/tests/test_sql_agent.py
git commit -m "feat(nl2sql): pipeline answer_structured (generar→validar→juez→retry→ejecutar)"
```

---

### Task 7: Síntesis grounded (`agents/sql_present.py`)

**Files:**
- Create: `backend/app/agents/sql_present.py`
- Test: `backend/tests/test_sql_present.py`

**Interfaces:**
- Consumes: `make_llm`, `get_settings`.
- Produces:
  - `SQL_EMPTY_MESSAGE: str`
  - `render_rows_markdown(rows: list[dict], columns: list[str]) -> str`
  - `async def synthesize_sql_answer(question: str, rows: list[dict], columns: list[str], llm: Any = None) -> str` (números verbatim; guard → render determinista; fila vacía → `SQL_EMPTY_MESSAGE`)

- [ ] **Step 1: Escribir los tests (fallan)**

Create `backend/tests/test_sql_present.py`:
```python
from app.agents import sql_present


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return _Msg(self._content)


async def test_empty_rows_returns_fixed_message() -> None:
    out = await sql_present.synthesize_sql_answer("¿cuántos?", [], [])
    assert out == sql_present.SQL_EMPTY_MESSAGE


async def test_scalar_answer_keeps_verbatim_number() -> None:
    out = await sql_present.synthesize_sql_answer(
        "¿cuántos turnos?", [{"total": 12}], ["total"],
        llm=FakeLLM("Tenés 12 turnos esta semana."),
    )
    assert out == "Tenés 12 turnos esta semana."


async def test_guard_falls_back_when_number_hallucinated() -> None:
    out = await sql_present.synthesize_sql_answer(
        "¿cuántos turnos?", [{"total": 12}], ["total"],
        llm=FakeLLM("Tenés 99 turnos."),  # 99 no está en las filas
    )
    assert out == "Resultado: 12"


def test_render_rows_markdown_builds_table() -> None:
    md = sql_present.render_rows_markdown(
        [{"full_name": "Ana"}, {"full_name": "Beto"}], ["full_name"]
    )
    assert "| full_name |" in md
    assert "| Ana |" in md and "| Beto |" in md
```

- [ ] **Step 2: Correr los tests (fallan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_present.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.agents.sql_present'`).

- [ ] **Step 3: Implementar la síntesis**

Create `backend/app/agents/sql_present.py`:
```python
import re
from typing import Any

from app.config import get_settings

SQL_EMPTY_MESSAGE = "No encontré resultados para esa consulta."

SYNTH_SYSTEM = (
    "Sos el asistente de una práctica profesional. Respondé en español SOLO con los datos "
    "provistos. No inventes ni calcules números nuevos. Si hay varias filas, podés mostrarlas "
    "en una tabla markdown. Sé breve."
)


def _default_llm() -> Any:
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model, temperature=0.1)


def render_rows_markdown(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return ""
    cols = columns or list(rows[0].keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join(
        "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows
    )
    return f"{header}\n{sep}\n{body}"


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def _grounded(answer: str, rows: list[dict]) -> bool:
    cells = [str(v) for r in rows for v in r.values()]
    n = str(len(rows))
    return all(any(num in c for c in cells) or num == n for num in _numbers(answer))


def _deterministic(rows: list[dict], columns: list[str]) -> str:
    cols = columns or list(rows[0].keys())
    if len(rows) == 1 and len(cols) == 1:
        return f"Resultado: {list(rows[0].values())[0]}"
    return render_rows_markdown(rows, columns)


async def synthesize_sql_answer(
    question: str, rows: list[dict], columns: list[str], llm: Any = None
) -> str:
    if not rows:
        return SQL_EMPTY_MESSAGE
    llm = llm or _default_llm()
    table = render_rows_markdown(rows, columns)
    messages = [("system", SYNTH_SYSTEM), ("human", f"Pregunta: {question}\n\nDatos:\n{table}")]
    resp = await llm.ainvoke(messages)
    answer = (getattr(resp, "content", "") or "").strip()
    if not answer or not _grounded(answer, rows):
        return _deterministic(rows, columns)
    return answer
```

- [ ] **Step 4: Correr los tests (pasan)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_present.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + typecheck + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format . ; backend\.venv\Scripts\python -m mypy app/ --config-file backend/pyproject.toml`
Expected: ruff clean; mypy `Success`.
```bash
git add backend/app/agents/sql_present.py backend/tests/test_sql_present.py
git commit -m "feat(nl2sql): síntesis grounded de filas (números verbatim + guard + tabla markdown)"
```

---

### Task 8: Nodo del grafo + wiring (`sql_node`)

**Files:**
- Modify: `backend/app/graph/state.py:9-33` (campos `candidate_sql`, `judge_scores`)
- Modify: `backend/app/graph/nodes.py` (reemplazar `sql_stub` por `sql_node`)
- Modify: `backend/app/graph/edges.py:3-9`
- Modify: `backend/app/graph/build.py:7-44`
- Modify: `backend/tests/test_state.py`, `backend/tests/test_nodes.py`, `backend/tests/test_graph.py`

**Interfaces:**
- Consumes: `answer_structured` (Task 6), `synthesize_sql_answer` (Task 7), `SqlResult`.
- Produces: `sql_node(state: AgentState) -> dict`; `AgentState` con `candidate_sql: str` y `judge_scores: dict`; ruteo `"sql" → "sql_node"`.

- [ ] **Step 1: Actualizar el test de state (falla)**

En `backend/tests/test_state.py`, agregar al final de `test_new_state_has_minimal_shape`:
```python
    assert s["candidate_sql"] == ""
    assert s["judge_scores"] == {}
```

- [ ] **Step 2: Actualizar `AgentState` y `new_state`**

En `backend/app/graph/state.py`, dentro del `TypedDict` `AgentState` (después de `sources: list[dict]`):
```python
    candidate_sql: str
    judge_scores: dict
```
Y en `new_state`, dentro del dict devuelto (después de `"sources": [],`):
```python
        "candidate_sql": "",
        "judge_scores": {},
```

- [ ] **Step 3: Reemplazar el test de `sql_stub` por tests de `sql_node` (fallan)**

En `backend/tests/test_nodes.py`, **eliminar** la función `test_sql_stub_streams_not_available` completa (la que aserta `nodes.STUB_MESSAGE` para `nodes.sql_stub`) y agregar al final del archivo:
```python
async def test_sql_node_emits_synthesized_answer(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql="SELECT 1", rows=[{"total": 12}], columns=["total"])

    async def _fake_synth(question, rows, columns, llm=None):
        return "Tenés 12 turnos esta semana."

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    monkeypatch.setattr(nodes, "synthesize_sql_answer", _fake_synth)
    tokens, sources = await _run(nodes.sql_node, new_state("¿cuántos turnos?", "p", "t"))
    assert tokens == "Tenés 12 turnos esta semana."
    assert sources == []


async def test_sql_node_abstains_with_no_sources(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql=None, abstained=True, reason="x")

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    tokens, sources = await _run(nodes.sql_node, new_state("algo raro", "p", "t"))
    assert tokens == nodes.SQL_ABSTAIN_MESSAGE
    assert sources == []
```

- [ ] **Step 4: Reemplazar `sql_stub` por `sql_node` en `nodes.py`**

En `backend/app/graph/nodes.py`, agregar imports arriba (después de `from app.graph.state import AgentState, last_user_text`):
```python
from app.agents.sql_agent import answer_structured
from app.agents.sql_present import synthesize_sql_answer
```
Agregar la constante (después de `SCOPE_MESSAGE = (...)`):
```python
SQL_ABSTAIN_MESSAGE = (
    "No pude traducir tu pregunta a una consulta segura sobre tus datos. ¿Podés reformularla?"
)
```
**Eliminar** la función `async def sql_stub(state: AgentState) -> dict:` completa y agregar en su lugar:
```python
async def sql_node(state: AgentState) -> dict:
    result = await answer_structured(last_user_text(state), state["practice_id"])
    if result.abstained:
        write_token(SQL_ABSTAIN_MESSAGE)
        write_sources([])
        answer = SQL_ABSTAIN_MESSAGE
    else:
        answer = await synthesize_sql_answer(
            last_user_text(state), result.rows, result.columns
        )
        for piece in _stream_chunks(answer):
            write_token(piece)
        write_sources([])
    return {
        "candidate_sql": result.sql or "",
        "judge_scores": {"sql_match": not result.abstained},
        "messages": [AIMessage(content=answer)],
    }
```

- [ ] **Step 5: Actualizar `edges.py` y `build.py`**

En `backend/app/graph/edges.py`, en `_INTENT_TO_NODE`:
```python
    "sql": "sql_node",
```
(reemplaza `"sql": "sql_stub"`).

En `backend/app/graph/build.py`, aplicar estos cuatro cambios (reemplazar cada literal `sql_stub` por `sql_node`):
```python
# 1) en el import desde app.graph.nodes:  sql_stub,  ->  sql_node,
from app.graph.nodes import (
    action_stub,
    chitchat_node,
    rag_node,
    scope_reject_node,
    sql_node,
)

# 2) la tupla de hojas:
_LEAF_NODES = ("rag", "chitchat", "scope_reject", "sql_node", "action_stub")

# 3) el registro del nodo (dentro de build_graph):
    g.add_node("sql_node", sql_node)

# 4) el path-map de add_conditional_edges (la línea del sql):
            "sql_node": "sql_node",
```
(`action_stub` queda igual en los cuatro lugares.)

- [ ] **Step 6: Actualizar `test_graph.py` (referencias a `sql_stub`)**

En `backend/tests/test_graph.py`:
- `test_route_maps_intents_to_nodes`: `edges.route({"intent": "sql"}) == "sql_node"`.
- `test_every_intent_maps_to_a_real_node`: en `valid_nodes`, cambiar `"sql_stub"` por `"sql_node"`.
- Reemplazar `test_graph_routes_sql_to_stub` por:
```python
async def test_graph_routes_sql_to_node(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql="SELECT 1", rows=[{"total": 3}], columns=["total"])

    async def _fake_synth(question, rows, columns, llm=None):
        return "Tenés 3 turnos."

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    monkeypatch.setattr(nodes, "synthesize_sql_answer", _fake_synth)
    tokens, sources = await _run_full(monkeypatch, "¿cuántos turnos?", "sql")
    assert tokens == "Tenés 3 turnos."
    assert sources == []
```

- [ ] **Step 7: Correr la suite no-llm completa (pasa)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (todo verde; los nuevos tests sumados, sin regresiones).

- [ ] **Step 8: Lint + typecheck + commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format . ; backend\.venv\Scripts\python -m mypy app/ --config-file backend/pyproject.toml`
Expected: ruff clean; mypy `Success`.
```bash
git add backend/app/graph/ backend/tests/test_state.py backend/tests/test_nodes.py backend/tests/test_graph.py
git commit -m "feat(nl2sql): sql_node reemplaza sql_stub (síntesis + emisión SSE) y wiring del grafo"
```

---

### Task 9: E2E LLM + golden set

**Files:**
- Create: `backend/tests/test_sql_e2e_llm.py`
- Modify: `backend/app/eval/golden_set.jsonl` (append casos `sql`)

**Interfaces:**
- Consumes: el grafo completo vía `app.main.app` (`/chat`), `seed_demo`, Ollama real.

- [ ] **Step 1: Sembrar datos para el e2e**

Run: `backend\.venv\Scripts\python backend\seed_demo.py`
Expected: `seed_demo: {'practitioners': 3, 'clients': 30, 'appointments': 80}`.

- [ ] **Step 2: Escribir el e2e (requiere Ollama + DB)**

Create `backend/tests/test_sql_e2e_llm.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient

from app import db
from app.main import app
from app.graph import nodes
from tests.test_e2e_llm import _parse_sse


@pytest.mark.llm
@pytest.mark.integration
async def test_real_llm_counts_appointments_this_week() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pool = await db.get_pool()
    expected = await pool.fetchval(
        "SELECT count(*) FROM appointments "
        "WHERE start_at >= date_trunc('week', now()) "
        "AND start_at < date_trunc('week', now()) + interval '7 days'"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with c.stream("POST", "/chat", json={"message": "¿cuántos turnos hay esta semana?"}) as resp:
            body = "".join([line + "\n" async for line in resp.aiter_lines()])
    answer, sources = _parse_sse(body)
    assert str(expected) in answer
    assert sources == []


@pytest.mark.llm
@pytest.mark.integration
async def test_real_llm_abstains_on_untranslatable_question() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with c.stream("POST", "/chat", json={"message": "sumá todos los números primos del universo"}) as resp:
            body = "".join([line + "\n" async for line in resp.aiter_lines()])
    answer, _ = _parse_sse(body)
    # router puede mandarlo a sql o a out_of_scope; en ambos casos no inventa datos
    assert answer.strip() != ""
    assert nodes.SQL_ABSTAIN_MESSAGE in answer or nodes.SCOPE_MESSAGE in answer
```

- [ ] **Step 3: Correr el e2e (pasa, requiere Ollama corriendo)**

Run: `backend\.venv\Scripts\python -m pytest backend/tests/test_sql_e2e_llm.py -m llm -q`
Expected: PASS (2 passed). Si Ollama no está, se deseleccionan con `-m "not llm"`.

- [ ] **Step 4: Agregar casos al golden set**

Append a `backend/app/eval/golden_set.jsonl`:
```jsonl
{"question": "¿cuántos turnos hay esta semana?", "category": "sql", "expected_behavior": "sql_answer", "gold_sql": "SELECT count(*) FROM appointments WHERE practice_id = '00000000-0000-0000-0000-000000000001' AND start_at >= date_trunc('week', now()) AND start_at < date_trunc('week', now()) + interval '7 days'"}
{"question": "listá los clientes activos", "category": "sql", "expected_behavior": "sql_answer", "gold_sql": "SELECT full_name FROM clients WHERE practice_id = '00000000-0000-0000-0000-000000000001' AND status = 'activo' LIMIT 200"}
```

- [ ] **Step 5: Commit**

Run: `cd backend; backend\.venv\Scripts\python -m ruff check . --fix ; backend\.venv\Scripts\python -m ruff format .`
```bash
git add backend/tests/test_sql_e2e_llm.py backend/app/eval/golden_set.jsonl
git commit -m "test(nl2sql): e2e LLM (conteo de turnos + abstención) y casos sql al golden set"
```

---

## Cierre del slice (verificación final, no es una tarea con commit)

Tras la Task 9, correr los gates completos (DoD CLAUDE.md §6):

- [ ] `cd backend; backend\.venv\Scripts\python -m ruff check . ; backend\.venv\Scripts\python -m ruff format .` → limpio.
- [ ] `backend\.venv\Scripts\python -m mypy app/ --config-file backend/pyproject.toml` → `Success`.
- [ ] `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → todo verde, sin regresiones.
- [ ] `backend\.venv\Scripts\python -m pytest backend/tests -m llm -q` → verde con Ollama + ambos modelos + DB sembrada.
- [ ] Smoke navegador (CLAUDE.md §2): `docker compose up -d`; `backend\.venv\Scripts\python backend\dev.py`; `npm --prefix frontend run dev`. Probar: `"hola"` (chitchat), una documental (RAG con citas), `"¿cuántos turnos esta semana?"` (conteo real). Confirmar que una acción de escritura sigue mostrando el stub / pidiendo confirmación (`action_stub` intacto).
- [ ] Merge a `main` (sin push, según workflow): `git checkout main; git merge --no-ff fase-1/nl2sql-data-agent`.
```

