# Praxia · Fase 1 · Slice 3 — Data Agent NL2SQL (lectura estructurada read-only)

> Diseño aprobado el 2026-06-26. Spec de un único slice implementable.
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md`.
> Slices previos: grafo + router (`docs/superpowers/specs/2026-06-25-grafo-router-design.md`),
> subgrafo CRAG (`docs/superpowers/specs/2026-06-26-crag-design.md`).

## Objetivo

Reemplazar el `sql_stub` de hoy (que solo emite "función no disponible") por un **Data Agent
read-only** que traduce preguntas en español a un `SELECT` validado, lo ejecuta contra Postgres
y responde en el chat (CLAUDE.md §4: "Lectura estructurada (NL2SQL)"). Entregable observable:

- *"¿cuántos turnos esta semana?"* → `SELECT COUNT(*) … WHERE start_at` esta semana → **"Tenés 12 turnos esta semana."**
- *"listá los clientes activos"* → filas → frase + **tabla markdown**.
- Pregunta intraducible con seguridad, o juez intención↔SQL en contra tras reintentos → **abstención**
  cordial (mismo espíritu fail-closed que CRAG); nunca se ejecuta SQL dudoso.

La estrategia NL→SQL elegida es **approach A** (CLAUDE.md §4 literal): el LLM genera el `SELECT`,
se valida duro, un juez verifica intención↔SQL, y recién entonces se ejecuta read-only.

## No-objetivos (diferidos, cada uno es trabajo propio posterior)

- **Tools de escritura con human-in-the-loop** (`create_appointment`, `log_interaction` detrás de
  `interrupt`): es el siguiente slice de Fase 1. Acá `action_stub` queda **intacto**.
- **Wrapper MCP `mcp_servers/mcp_postgres.py`** (la tool `semantic_query` de Blueprint §8.2): el
  Data Agent se construye **in-process**; el nodo lo llama directo. El wrapper MCP llega cuando lo
  pidan las write-tools o la introspección del dev loop (CLAUDE.md §8). Razón: §7 "no construyas
  de más" — la indirección MCP no cambia la lógica de lectura.
- **Prompts compilados con DSPy** (MIPROv2/GEPA) para generación SQL / síntesis / juez — Fase 2.
  Acá se escriben a mano (igual que el router del Slice 1); se recompilan contra el golden set en F2.
- **RLS multi-tenant en Postgres** — Fase 4 (CLAUDE.md §7). Acá el aislamiento por `practice_id`
  es **app-level** (validación + transacción READ ONLY + juez), honestamente no a prueba de balas.
- **Gate formal de Ragas / execution-accuracy en CI** — Fase 2. Acá se agregan casos `category:'sql'`
  al golden set y un helper de comparación de filas, pero la suite como gate llega en F2.
- **Modelado semántico de `invoices` / `interactions`** y métricas de facturación/CRM — fuera de
  alcance: este slice modela **solo `appointments` + `clients`** (+ `practitioners` como join).
- **Surface del SQL/tabla en un canvas rico** (vista de tabla, SQL colapsable): el `candidate_sql`
  se guarda en el state para auditoría/eval, pero la UI rica es el ítem de frontend diferido de F1.
  La tabla se entrega como **markdown** dentro de la respuesta (el `<Thread>` actual ya lo renderiza).

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). Inferencia 100% local por Ollama:
`gemma4:12b` genera SQL y sintetiza la respuesta; `gemma4:e4b` juzga intención↔SQL. El grafo es la
fuente de control: el Data Agent se enchufa como el nodo `sql` detrás del router; no se agregan
caminos que esquiven router ni guardrails. **Lectura y escritura separadas por diseño**: este slice
es **solo lectura** (`SELECT`); las escrituras nunca son SQL libre (slice siguiente). Aislamiento
por `practice_id` en toda query (CLAUDE.md §0.5).

## Arquitectura

### Decisión de límites (la que más define el diseño)

El **Data Agent es una función pura de negocio** (`agents/sql_agent.py`) que devuelve un
`SqlResult` (datos, sin efectos): `generar → validar → juzgar → [reintentar | abstener] → ejecutar`.
**Todo** el streaming SSE (`write_token` / `write_sources`) y la **síntesis** de la respuesta en
lenguaje natural los hace el `sql_node` externo, una sola vez, después de tener las filas. Mismo
patrón que el Slice 2 (subgrafo CRAG puro + `rag_node` que emite). Razones:

- La síntesis necesita las **filas ya ejecutadas** (grounding sobre datos reales) → ocurre en el
  nodo, no en el agente.
- Nodo testeable de la emisión por separado; agente testeable sin mocks de streaming.
- Evita propagar el stream-writer dentro de la lógica del agente.

### Módulos

```
backend/
├── seed_demo.py                      # NUEVO: seeder Faker (es_AR, semilla fija), idempotente.
└── app/
    ├── schema.sql                    # +tabla appointments (Blueprint §5.2), idempotente.
    ├── config.py                     # +sql_row_limit, sql_timeout_ms, sql_max_attempts.
    ├── db.py                         # +run_select(): ejecutor READ ONLY (timeout + tope de filas).
    ├── semantic_layer/               # NUEVO
    │   ├── __init__.py
    │   ├── model.yaml                # entities/metrics/dimensions/glossary (Blueprint §5.3, acotado).
    │   └── resolver.py               # parseo del yaml + introspección de columnas + contexto + allow-list.
    ├── agents/                       # NUEVO (hoy vacío en el repo; lo crea este slice)
    │   ├── __init__.py
    │   └── sql_agent.py              # Data Agent: generar→validar→juez→retry/abstención→ejecutar. PURO.
    └── graph/
        ├── state.py                  # +candidate_sql, +judge_scores en AgentState.
        ├── nodes.py                  # sql_node reemplaza sql_stub: invoca el agente, SINTETIZA, EMITE.
        ├── edges.py                  # _INTENT_TO_NODE["sql"] = "sql_node".
        └── build.py                  # registra sql_node en vez de sql_stub.
```

- **Regla CLAUDE.md §3**: un nodo = una función pura testeable; la lógica de negocio vive en
  `agents/` (acá sí, a diferencia de RAG que ya vivía en `rag/`). El blueprint ubica el Data Agent
  en `agents/sql_agent.py`: este slice **estrena `agents/`**.
- La validación de SQL vive en `agents/sql_agent.py` como funciones puras (`validate_select`), no en
  un módulo aparte: es chica y solo la usa el agente. Si crece, se extrae después.

## Flujo de datos

```
                ┌──────────────── loop (sql_max_attempts = 2) ────────────────┐
 question       │                                                             │
   │            ▼                                                             │
   ▼   generar SELECT (gemma4:12b, structured) ── feedback del fallo ◄────────┤
 (practice_id)            │                                                   │
                          ▼                                                   │
                   validar (sqlglot + guards)                                 │
                     ok │      │ inválido ──────────► ¿quedan intentos? ──────┘ sí
                        ▼      └────────────────────────────► no ──► ABSTAIN
                 juez intención↔SQL (gemma4:e4b)
                  match │   │ no-match ─────────────► ¿quedan intentos? ───────► no ──► ABSTAIN
                        ▼                                   │ sí ──────────────────────┘
                 run_select (READ ONLY, timeout, LIMIT)
                        │
                        ▼
                 SqlResult{sql, rows, columns}  ──► sql_node sintetiza (grounded) + emite SSE
```

Reglas del flujo:
- **Cota dura de 2 intentos de generación** (1 retry). El retry recibe el SQL fallido + la razón
  (validación o juez) para corregir. Cap análogo a `rag_max_attempts` del Slice 2.
- Cualquier excepción en generar/validar/juzgar cuenta como intento fallido (fail-closed); agotados
  los intentos → abstención.
- Error de ejecución (timeout, error SQL) → abstención con mensaje honesto; el detalle se **loguea**,
  no se muestra al usuario (no filtrar SQL/errores crudos).
- Resultado vacío **no** es abstención: es una respuesta honesta (*"No encontré turnos para esta semana."*).

## Cimiento de datos

### `schema.sql` — tabla `appointments` (Blueprint §5.2, idempotente)

Se agrega **tal cual** el blueprint, con `IF NOT EXISTS` (convención del schema.sql actual):

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
    channel         TEXT,                       -- 'presencial' | 'telellamada'
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_appt_practice_date ON appointments(practice_id, start_at);
CREATE INDEX IF NOT EXISTS idx_appt_client ON appointments(client_id);
```

Solo esta tabla. `interactions`/`invoices`/`memories`/`agent_runs` del blueprint quedan para sus
slices (sin queries en alcance que las toquen).

### `seed_demo.py` — datos sintéticos (CLAUDE.md §7: Faker + Gemma)

Script standalone (`backend\.venv\Scripts\python backend\seed_demo.py`), usa el pool de `db.py`.
**Faker (locale `es_AR`) + `Faker.seed_instance(...)` y `random.seed(...)` fijos** → reproducible.

- **practitioners**: ~3, UUID determinístico (`uuid5`), `ON CONFLICT (id) DO NOTHING`.
- **clients**: ~30, UUID determinístico, mezcla `status` activo/inactivo/baja, `ON CONFLICT DO NOTHING`.
- **appointments**: ~80, **`DELETE FROM appointments WHERE practice_id = demo` y reinsertar** (no
  referenciadas por otras tablas en este schema). `start_at` **relativo a `now()`** con offsets
  determinísticos (algunos esta semana, otros semanas/meses atrás y adelante), `end_at = start_at +
  30/45 min`, `status`/`reason`/`practitioner`/`client` variados. Así *"esta semana"* da un número no
  trivial cuando sea que corra el demo (el ancla es `now()`; los offsets son fijos).
- Faker es **dev-only** (no entra al runtime del producto). `reason` se rellena con frases cortas de
  Faker; usar Gemma para texto es polish opcional, no parte de este slice.

## Capa semántica — `app/semantic_layer/`

### `model.yaml` (forma Blueprint §5.3, acotado a este slice)

```yaml
entities:
  appointments: { table: appointments, time_dimension: start_at }
  clients:      { table: clients,      time_dimension: created_at }

metrics:
  turnos_totales:   { sql: "COUNT(*)", from: appointments, synonyms: ["turnos","citas"] }
  ausencias:        { sql: "COUNT(*) FILTER (WHERE status = 'ausente')", from: appointments,
                      synonyms: ["ausentes","no shows","faltas","inasistencias"] }
  clientes_activos: { sql: "COUNT(*) FILTER (WHERE status = 'activo')", from: clients,
                      synonyms: ["pacientes activos","clientes activos"] }

dimensions:
  por_profesional: { sql: "practitioners.full_name",
                     join: "JOIN practitioners ON appointments.practitioner_id = practitioners.id" }
  por_semana:      { sql: "date_trunc('week', start_at)" }
  por_mes:         { sql: "date_trunc('month', start_at)" }
  por_estado:      { sql: "status" }

glossary:
  paciente: clients
  pacientes: clients
  cliente: clients
  turno: appointments
  cita: appointments
```

> En approach A el `model.yaml` es **contexto descriptivo** que guía al LLM (no la entrada de un
> compilador). Las tablas tenant del alcance — `appointments`, `clients`, `practitioners` — todas
> tienen `practice_id`.

### `resolver.py` — contrato

Partes **puras** (testeables sin DB) separadas de la **introspección** (única parte con I/O):

```python
@dataclass(frozen=True)
class SemanticLayer:
    schema_context: str                       # DDL/columnas de las tablas permitidas (para el prompt)
    semantic_context: str                     # métricas/dimensiones/glosario renderizados
    allowed_tables: frozenset[str]            # {"appointments","clients","practitioners"}
    allowed_columns: dict[str, frozenset[str]]  # por tabla (de la introspección)

def parse_model_yaml(path: str | None = None) -> dict    # PURA: yaml.safe_load (default = model.yaml de al lado)
def allowed_tables_from(spec: dict) -> frozenset[str]    # PURA: entities + tablas de los joins
def render_semantic_context(spec: dict) -> str           # PURA: métricas/dimensiones/glosario → texto
async def introspect_columns(pool, tables) -> dict[str, frozenset[str]]   # I/O: information_schema.columns
async def load_semantic_layer(pool) -> SemanticLayer     # compone; memoiza en un global protegido
```

- `parse_model_yaml` usa **`yaml.safe_load`** (dep PyYAML; ver Dependencias).
- `allowed_tables` se deriva del yaml (entities + tablas mencionadas en los `join` de dimensions) →
  no se hardcodea la lista en dos lados.
- `schema_context` se arma de la **introspección** (`information_schema.columns` filtrado a
  `allowed_tables`) para mantenerse en sync con `schema.sql` (Blueprint: "esquema introspectado").
- `load_semantic_layer` memoiza el resultado (el esquema no cambia en runtime); el `pool` se pasa
  explícito para no acoplar el resolver al singleton.

## Data Agent — `app/agents/sql_agent.py`

```python
@dataclass
class SqlResult:
    sql: str | None
    rows: list[dict]
    columns: list[str]
    abstained: bool
    reason: str                     # diagnóstico interno (log/eval), no se muestra crudo

class SqlDraft(BaseModel):    sql: str                       # structured output del generador
class SqlIntentVerdict(BaseModel): matches: bool; reason: str  # structured output del juez

async def answer_structured(
    question: str, practice_id: str, *, pool=None, gen_llm=None, judge_llm=None,
) -> SqlResult
```

### Generación (`gemma4:12b`, **texto plano** + validación sqlglot)
Prompt (a mano, español) con: `schema_context`, `semantic_context`, el `practice_id` **literal**, y
la pregunta. Instrucciones: **solo un `SELECT`** (sin comentarios, sin múltiples sentencias);
**filtrá siempre por `practice_id = '<uuid>'`**; usá las métricas/dimensiones/glosario como guía;
para "esta semana" usá `date_trunc('week', now())`.

> **Nota de implementación (post-build, commit `8a270bb`):** el diseño original generaba con
> `with_structured_output(SqlDraft)`, pero el e2e reveló que **Gemma local devuelve `None`** por esa
> vía: el 12b emite el `SELECT` como **texto plano** (sin tool-call), y `method="json_schema"`
> *degrada* el SQL. Por eso la generación toma el `content` crudo (`_extract_sql`, que pela un bloque
> ```` ```sql ```` y el `;` final) y lo pasa a `validate_select`. **`sqlglot` es la decodificación
> restringida real de esta ruta** (CLAUDE.md §4: no hay regex sobre JSON; el validador hace el
> control estructural). El **juez** y el **router** sí mantienen `with_structured_output` (el
> veredicto booleano sí emite tool-call). `SqlDraft` se eliminó.

### Validación — `validate_select(sql, allowed_tables, practice_id) -> ValidationResult` (sqlglot)
"No reinvento parser" (§0.3). Con `sqlglot` (dialecto `postgres`):
1. `sqlglot.parse(sql, read="postgres")` → **exactamente una** sentencia.
2. La sentencia top es `exp.Select` (o `exp.With` que envuelve un `Select`). **Rechazar** si el árbol
   contiene cualquier nodo de escritura/DDL (`Insert/Update/Delete/Merge/Create/Drop/Alter/Command/
   Grant/…`).
3. Toda tabla (`exp.Table`) ∈ `allowed_tables`; si no, rechazar.
4. **Tenant**: el árbol debe contener al menos un predicado de igualdad `practice_id = '<practice_id>'`
   con el literal correcto (la tabla conductora). Reforzar a "toda tabla tenant del FROM/JOIN" es un
   endurecimiento barato que se incluye si el árbol lo permite sin fragilidad. Backstop duro =
   transacción READ ONLY (abajo) + RLS en Fase 4.
5. **LIMIT**: si el `Select` no tiene `limit`, inyectar `LIMIT sql_row_limit`; si tiene uno mayor al
   tope, bajarlo al tope. Re-renderizar con `.sql(dialect="postgres")` (SQL normalizado, una sentencia).
- Funciones peligrosas (`pg_sleep`, etc.) las cubre el `statement_timeout` del ejecutor; no se
  construye deny-list explícita en este slice.

### Juez intención↔SQL (`gemma4:e4b`, `with_structured_output(SqlIntentVerdict)`)
"¿este `SELECT` responde lo que pidió el usuario?" → `matches: bool` + `reason`. Si `False`, cuenta
como intento fallido → retry con feedback o abstención. LLM inyectable (patrón `classify_intent`).

### Ejecución
Llama a `db.run_select(sql, timeout_ms=settings.sql_timeout_ms, row_limit=settings.sql_row_limit)`.

### Abstención / errores (fail-closed)
SQL inválido tras 2 intentos, o juez en contra tras 2 intentos, o excepción de ejecución →
`SqlResult(sql=…, rows=[], columns=[], abstained=True, reason=…)`. El nodo traduce eso a un mensaje
cordial fijo (no se expone `reason` crudo).

## Ejecutor read-only — `db.py::run_select`

```python
async def run_select(sql: str, *, timeout_ms: int, row_limit: int) -> tuple[list[dict], list[str]]
```
- `async with conn.transaction(readonly=True):` — `START TRANSACTION … READ ONLY`: aunque algo se
  filtre por la validación, la transacción **no puede escribir** (defensa en profundidad).
- `await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")` (int-cast, sin interpolar
  texto libre) → corta queries lentas / `pg_sleep`.
- `rows = await conn.fetch(sql)`; recortar defensivamente a `row_limit` (el `LIMIT` ya fue inyectado);
  `columns` de las claves de la primera fila (o `[]`).
- El SQL va sin parámetros (literal ya validado). **Honestidad**: los literales no van parametrizados;
  la inyección-a-escritura la bloquean la transacción READ ONLY + la validación de sentencia única;
  la fuga cross-tenant, la allow-list + el predicado `practice_id`. RLS (F4) es el cierre real.

## Nodo del grafo + presentación — `graph/nodes.py::sql_node`

Reemplaza `sql_stub`. Sintetiza **grounded** y emite (mismo buffer-then-stream que `rag_node`):

```python
async def sql_node(state: AgentState) -> dict:
    result = await answer_structured(last_user_text(state), state["practice_id"])
    if result.abstained:
        write_token(SQL_ABSTAIN_MESSAGE); write_sources([])
        answer = SQL_ABSTAIN_MESSAGE
    else:
        answer = await synthesize_sql_answer(last_user_text(state), result.rows, result.columns)
        for piece in _stream_chunks(answer):
            write_token(piece)
        write_sources([])                       # el SQL no tiene "fuentes" tipo documento
    return {"candidate_sql": result.sql, "judge_scores": {"sql_match": not result.abstained},
            "messages": [AIMessage(content=answer)]}
```

### Síntesis grounded — `synthesize_sql_answer(question, rows, columns)` (`gemma4:12b`)
- Render de `rows` como tabla markdown compacta en el prompt; instrucción: **respondé en español
  usando SOLO estos datos; no inventes ni calcules números nuevos**.
- **Guard de groundedness (barato)**: extraer los números de la respuesta (`\d+([.,]\d+)?`); si alguno
  no aparece entre los valores de las filas **ni** equivale a `len(rows)`, **caer a render
  determinista** (escalar → frase simple `"Resultado: …"`; multi-fila → tabla markdown). La garantía
  fuerte es que la síntesis solo ve las filas; el guard es cinturón-y-tiradores para el caso estrella
  (el conteo de turnos).
- Multi-fila → frase + **tabla markdown** (la renderiza el `<Thread>` actual; sin nuevo evento SSE).
- Escalar de una celda → frase natural (*"Tenés 12 turnos esta semana."*).

### `state.py`
`AgentState` suma `candidate_sql: str` y `judge_scores: dict` (campos ya previstos en el blueprint);
se mantiene mínimo. `new_state` los inicializa (`""` / `{}`).

### `edges.py` / `build.py`
`_INTENT_TO_NODE["sql"] = "sql_node"`; en `build.py` registrar `sql_node` y reemplazar en
`_LEAF_NODES`. `action` sigue ruteando a `action_stub` (intacto).

## Config nueva (`config.py`)

| Var | Default | Para qué |
|---|---|---|
| `sql_row_limit` | `200` | tope de filas (LIMIT inyectado + recorte en el fetch) |
| `sql_timeout_ms` | `5000` | `statement_timeout` de la transacción read-only |
| `sql_max_attempts` | `2` | intentos de generación (1 retry con feedback) |

Modelos: generación y síntesis con `ollama_model` (`gemma4:12b`); juez con `gemma4:e4b` (clasificación
liviana; el router ya prueba `with_structured_output` en e4b). Todo local por Ollama.

## Multi-tenant (CLAUDE.md §0.5)

`practice_id` viaja en `AgentState` (hoy de `settings`, single-tenant en dev) → al prompt como literal
→ el validador exige el predicado → el ejecutor corre read-only. Toda tabla del alcance es
tenant-scoped. **Pre-RLS el aislamiento es app-level** (validación + read-only + juez), no a prueba de
balas: RLS en Postgres es el cierre real y queda en Fase 4 (CLAUDE.md §7).

## Seguridad / guardrails

- **Read-only en capas**: validación sqlglot (sentencia única, solo SELECT, allow-list) + transacción
  `READ ONLY` + `statement_timeout` + tope de filas.
- **PII (datos de salud)**: las filas pueden traer nombres de pacientes (PII). Van al usuario
  (autorizado), pero **no se loguean crudas**: el log registra `sql` + cantidad de filas + veredicto,
  nunca el contenido de las filas. La redacción Presidio en la **entrada** es el slice de guardrails
  (Fase 1 posterior); este slice no la agrega, solo evita filtrar PII a logs.
- **Inyección**: la pregunta del usuario nunca se concatena a SQL; la media el LLM y la valida sqlglot.
- El **juez intención↔SQL** es el gate semántico antes de ejecutar (CLAUDE.md §4).

## Testing (DoD CLAUDE.md §6)

Patrón establecido: inyección de `llm=`/`pool=` y `monkeypatch` de funciones de módulo
(`tests/test_router.py`, `tests/test_nodes.py`).

- **No-llm** (sin Ollama, sin DB salvo donde se note):
  - `test_sql_validator.py`: acepta un `SELECT` válido con `practice_id`; **rechaza** `INSERT`/`UPDATE`/
    `DELETE`/`DROP`, multi-sentencia, tabla fuera de allow-list, y SQL sin predicado `practice_id`;
    **inyecta** `LIMIT` cuando falta; **baja** un `LIMIT` mayor al tope. (sqlglot, puro, sin DB.)
  - `test_semantic_layer.py`: `parse_model_yaml` (shape), `allowed_tables_from`, `render_semantic_context`
    (puros, sin DB). La introspección se cubre bajo `-m llm`/DB o con un fake de columnas.
  - `test_sql_agent.py`: con `gen_llm`/`judge_llm` fakes (estilo `FakeRouterLLM`) y `run_select` +
    `load_semantic_layer` monkeypatcheados (este último a un `SemanticLayer` fake → test sin DB) →
    `SqlResult` feliz; **retry** (1er SQL inválido → regenera → ok); **abstención** tras cap;
    **juez-no** → abstención.
  - `test_synthesize_sql.py`: escalar → frase con el número **verbatim**; multi-fila → tabla markdown;
    **guard** → fallback determinista cuando el LLM fake mete un número que no está en las filas.
  - `test_nodes.py` (extender): `sql_node` feliz emite la frase y `write_sources([])`; abstención emite
    `SQL_ABSTAIN_MESSAGE` + `write_sources([])` (reusa el helper `_run` con `stream_mode="custom"`).
- **`-m llm`** (`test_sql_e2e_llm.py`; Ollama + Postgres reales, `seed_demo.py` corrido):
  *"¿cuántos turnos esta semana?"* → la respuesta contiene el **conteo real** (execution accuracy);
  *"listá los clientes activos"* → tabla; pregunta intraducible → abstención.
- **Golden set** (`app/eval/golden_set.jsonl`): agregar casos `category:'sql'` con `gold_sql`; helper de
  **execution accuracy** (corre `gold_sql` vs el generado, compara conjuntos de filas). El gate Ragas
  formal es F2; los casos quedan listos.
- **Gates**: `ruff check . && ruff format .`; `mypy` **siempre con `--config-file backend/pyproject.toml`**
  (sin eso da falso-positivo `asyncpg [import-untyped]`); `pytest -q` (no-llm) verde. Smoke §2: ahora
  *"¿cuántos turnos esta semana?"* devuelve un conteo real (no el stub) y las **escrituras siguen
  pidiendo confirmación** (`action_stub` intacto).

## Dependencias

| Dep | Tipo | Por qué |
|---|---|---|
| `sqlglot` | runtime | parseo/validación/normalización del `SELECT` (no reinventar parser, §0.3) |
| `PyYAML` | runtime | `yaml.safe_load` del `model.yaml` (hoy solo transitivo vía langchain; se fija explícito) |
| `Faker` | dev | seeder sintético (`seed_demo.py`); no entra al runtime del producto |

Todas OSS, $0, pip. Red solo para instalarlas una vez (CLAUDE.md §0). Sin servicios cloud, sin APIs
pagas, sin red saliente fuera de Ollama/Postgres/Qdrant locales (DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff`, `mypy --config-file backend/pyproject.toml` y `pytest -q` (no-llm) verdes; `-m llm` verde con
   Ollama + ambos modelos + infra + `seed_demo.py` corrido.
2. Tocamos SQL/síntesis/router-destino: la suite offline no regresiona; se agregan casos `sql` al
   golden set con execution accuracy.
3. Tocamos el grafo: el smoke de §2 pasa (chitchat / RAG con citas / **SQL con conteo real**) y las
   **escrituras siguen pidiendo confirmación** (`action_stub` intacto).
4. Prompts de alto apalancamiento (generación SQL, síntesis, juez) escritos a mano ahora; recompilar
   con DSPy queda anotado para Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales.
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **El 12b genera SQL incorrecto** (el punto frágil de approach A): mitigado por validación dura + juez
  intención↔SQL + cap de reintentos + execution accuracy en el golden set. Si el golden set muestra que
  es poco confiable, el **fallback principista es approach B** (query-spec + compilador determinista),
  que queda explícitamente en la manga para Fase 2.
- **Aislamiento tenant pre-RLS**: app-level (validación + read-only + juez), honestamente no a prueba de
  balas; RLS en Fase 4 es el cierre. Mientras tanto, allow-list chica (3 tablas) + predicado obligatorio.
- **Literales sin parametrizar**: inyección-a-escritura bloqueada por transacción READ ONLY + sentencia
  única; cross-tenant por allow-list + `practice_id`. Aceptable en F1; RLS lo cierra.
- **Fragilidad del e4b/12b en salida estructurada** (CLAUDE.md §9): decodificación restringida (no
  regex); casos límite → golden set; DSPy en F2.
- **Edge cases de dialecto en sqlglot**: fijar `read/write="postgres"`; casos problemáticos → golden set.
- **Latencia local** (generar 12b + juez e4b + posible retry + síntesis 12b): cap de 2 intentos, juez en
  e4b. Si pesa, semantic cache es el paso natural de F2.
- **Determinismo del seed vs fechas relativas**: los offsets de `start_at` son fijos por la semilla; el
  ancla es `now()` → *"esta semana"* siempre tiene datos sin volverse stale.
```
