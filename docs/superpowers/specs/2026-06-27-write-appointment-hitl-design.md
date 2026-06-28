# Praxia · Fase 1 · Slice 4 — Tool de escritura `create_appointment` con human-in-the-loop

> Diseño aprobado el 2026-06-27. Spec de un único slice implementable.
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md`.
> Slices previos: grafo + router (`2026-06-25-grafo-router-design.md`),
> subgrafo CRAG (`2026-06-26-crag-design.md`),
> Data Agent NL2SQL read-only (`2026-06-26-nl2sql-data-agent-design.md`).

## Objetivo

Reemplazar el `action_stub` de hoy (que solo emite "función no disponible") por la **primera tool
de escritura real** de Praxia: `create_appointment`, detrás de un **`interrupt` de LangGraph con
tarjeta de confirmación** (CLAUDE.md §4: "Escritura: nunca SQL libre. Solo tools parametrizadas y
siempre detrás de un `interrupt` que abre tarjeta de confirmación; el LLM *propone*, el humano
confirma, recién ahí se escribe"). Entregable observable:

- *"agendá un turno para \<cliente\> mañana a las 10"* → el grafo **propone** el turno resuelto
  (cliente, profesional, fecha/hora) y **se pausa** mostrando una **tarjeta** *"Crear turno: Ana
  López con la Dra. Gómez — mar 30/6 10:00–10:30. ¿Confirmás?"* con botones **Confirmar / Cancelar**.
- **Confirmar** → se escribe la fila en `appointments` (tool parametrizada) → recibo *"✅ Turno
  creado: …"*.
- **Cancelar** → no se escribe nada → *"Cancelado, no creé el turno."*.
- Datos no resolubles con seguridad (cliente inexistente/ambiguo, profesional ambiguo, fecha
  inentendible) → **abstención fail-closed** cordial, **sin** abrir tarjeta y **sin** escribir.

El gate de Fase 1 que cierra este slice (CLAUDE.md §2 / §6.3, Blueprint §6 criterio de aceptación):
**"cero acciones de escritura sin confirmación"** — y el smoke deja de ver el stub.

## No-objetivos (diferidos, cada uno es trabajo propio posterior)

- **`log_interaction` + tabla `interactions`** (Blueprint §5.2 "el corazón del CRM"): fast-follow una
  vez que exista la maquinaria HITL. Acá NO se crea la tabla `interactions` ni la tool. La maquinaria
  (interrupt + resume + tarjeta) queda lista para que la 2da tool sea barata.
- **Botón "Editar"** de la tarjeta (Blueprint §2.2 lista Confirmar/**Editar**/Cancelar): implica un
  form de re-edición / slot-filling → alcance propio. Acá **solo Confirmar / Cancelar**.
- **`agent_runs` / audit log + `consents`** (CLAUDE.md §5, Blueprint §5.2): hoy NO existen esas tablas
  y el read-path tampoco audita. La fila creada (con `created_at`) es el registro por ahora. Audit
  formal y enforcement de consentimiento = trabajo posterior (consistente con cómo se difirió
  multi-tenant/RLS).
- **Slot-filling multi-turno** (preguntar de a uno los campos faltantes y recordar la conversación):
  requiere `thread_id` estable + memoria. Acá la propuesta es **one-shot**: si falta un dato, abstiene.
- **`thread_id` estable multi-turno / memoria de corto plazo real**: este slice **sí** hace
  *round-trip* del `thread_id` para poder reanudar el `interrupt`, pero **no** persiste memoria entre
  mensajes distintos (cada `/chat` sigue minteando su `thread_id`). La memoria multi-turno es su
  propio slice.
- **Servidor MCP `mcp_servers/mcp_postgres.py`** (Blueprint §8.2 lista `create_appointment` como tool
  de `mcp-postgres`): igual que el Data Agent del Slice 3, la tool se construye **in-process**
  (`db.create_appointment` + `agents/action_agent.py`); el nodo la llama directo. El contrato §4 se
  cumple en espíritu (tool **parametrizada**, no SQL del LLM, + HITL). El wrapper MCP llega cuando lo
  pida la introspección del dev loop (§8) o prod. Razón: §7 "no construyas de más".
- **Migración a `@assistant-ui/react-ui` + canvas rico** (tablas/fichas/citas): la tarjeta se hace
  **mínima funcional** sobre el `useLocalRuntime` actual. El canvas rico sigue diferido en Fase 1.
- **Timezone por práctica**: MVP guarda en `appointments` (TIMESTAMPTZ) el instante absoluto que computa el LLM a partir del `now` (UTC), y **la tarjeta y el recibo etiquetan la hora como `(UTC)`** para no inducir a error. Mostrar/almacenar en la hora local de la práctica + DST = endurecimiento posterior (Fase 4).
- **Reprogramar / cancelar turnos existentes** (`update_appointment`): este slice solo **crea**.

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). Inferencia 100% local por Ollama:
`gemma4:12b` extrae los argumentos estructurados de la frase; **no hay LLM en el resolver ni en la
escritura** (determinísticos). El grafo es la fuente de control: la tool se enchufa detrás del router
(intent `action`); ningún camino esquiva router/guardrails. **Lectura y escritura separadas por
diseño**: la escritura nunca es SQL libre — es una tool parametrizada y **siempre** detrás del
`interrupt` de confirmación. Aislamiento por `practice_id` en resolución y en el INSERT (CLAUDE.md §0.5).

## Arquitectura

### Decisión de límites #1 — DOS nodos, no uno (la que protege la integridad del HITL)

`interrupt()` de LangGraph **re-ejecuta el nodo completo desde el principio** al reanudar: en la 1ª
corrida `interrupt(payload)` *levanta* `GraphInterrupt` y pausa; al reanudar con `Command(resume=x)`
el nodo corre **otra vez de arriba** y `interrupt()` **devuelve** `x`. Consecuencia crítica: todo lo
que esté **antes** del `interrupt` corre **dos veces**. Si la generación de la propuesta (llamada al
LLM, no determinística) viviera en el mismo nodo que el `interrupt`, al confirmar **se re-generaría** →
desperdicio de cómputo y, peor, **el humano podría terminar confirmando una propuesta distinta a la
que vio** (rompe el HITL).

Por eso el flujo se parte en **dos nodos**:

1. **`propose_appointment`** (sin `interrupt`): llama al LLM (args estructurados) + **resolver
   determinístico** (nombre→UUID, fecha→ISO). Devuelve `{"proposed_action": {...}}` o, si no resuelve,
   abstiene (emite mensaje, `proposed_action = None`). Como **retorna normalmente, se checkpointea**.
2. **`confirm_appointment`** (con `interrupt`): `decision = interrupt(state["proposed_action"])`; al
   reanudar, ejecuta `db.create_appointment(...)` con la propuesta **ya checkpointeada** (no recomputa)
   y emite el recibo, o cancela.

Al reanudar solo re-corre `confirm_appointment`; `propose_appointment` ya está en el checkpoint → **la
propuesta confirmada es exactamente la que se escribe**.

### Decisión de límites #2 — agente puro de negocio vs. nodo que orquesta

Igual patrón que Slices 2/3: la **lógica de negocio** (extracción + resolución) vive en
`agents/action_agent.py` y devuelve **datos** (un `ProposalResult`); el **nodo** hace el streaming SSE,
el `interrupt` y la llamada a la tool de escritura. El resolver **sí tiene I/O** (consulta `clients`/
`practitioners` para resolver nombres), por eso recibe el `pool` inyectable (testeable sin DB con un
fake), igual que `answer_structured` recibe `pool`/`gen_llm`.

### Módulos

```
backend/
└── app/
    ├── schema.sql                 # SIN CAMBIOS: appointments ya existe (Slice 3). created_by es nullable.
    ├── config.py                  # +appt_default_duration_min (30), +appt_name_match_limit (5).
    ├── db.py                      # +find_clients_by_name, +list_active_practitioners,
    │                              #  +find_practitioners_by_name, +create_appointment (INSERT guarded).
    ├── agents/
    │   ├── action_agent.py        # NUEVO: propose_appointment (LLM args + resolver determinístico). 
    │   └── __init__.py
    └── graph/
        ├── state.py               # +proposed_action: dict | None  (campo ya previsto en el blueprint).
        ├── nodes.py               # propose_appointment + confirm_appointment reemplazan action_stub.
        ├── edges.py               # "action" → "propose_appointment"; +route_after_propose.
        └── build.py               # registra los 2 nodos, quita action_stub, cablea el conditional.
    └── main.py                    # /chat: surface thread_id + emite evento SSE `confirm`;
                                   #  +POST /chat/resume {thread_id, decision} → Command(resume=...).
frontend/
├── lib/chatStream.ts             # +ChatEvent `confirm`; +resumeChat(thread_id, decision).
├── lib/runtime.ts                # detecta `confirm` → expone pendingAction (callback/estado).
├── components/ConfirmCard.tsx    # NUEVO: tarjeta Confirmar/Cancelar (mínima funcional).
└── app/page.tsx                  # estado pendingAction + render ConfirmCard + wiring del resume.
```

- **Regla CLAUDE.md §3**: un nodo = una función pura/testeable; la lógica vive en `agents/`. Este
  slice agrega `action_agent.py` al `agents/` que estrenó el Slice 3.
- `schema.sql` **no cambia**: `appointments` (con `client_id`, `practitioner_id`, `start_at`,
  `end_at`, `status DEFAULT 'programado'`, `reason`, `channel`, `created_by` nullable) ya está. No se
  crea ninguna tabla nueva.

## Flujo de datos

```
 "agendá un turno para Ana mañana 10:00"   (practice_id, now=UTC)
        │
        ▼   router (intent = action)
 propose_appointment ───────────────────────────────────────────────┐
        │  1) LLM gemma4:12b structured → ProposedAppointment        │  (UNA sola vez; se checkpointea)
        │     {client_name, practitioner_name?, start_at(ISO),       │
        │      duration_min, reason?, channel?}                       │
        │  2) resolver determinístico (sin LLM, scoped practice_id):  │
        │     client_name → client_id (0→no encontrado, >1→ambiguo)  │
        │     practitioner → id (None y 1 activo → default; si no →   │
        │                        pedir cuál)                          │
        │     start_at parse/valida; end_at = start + duration        │
        ▼                                                            │
   ¿resolvió?                                                        │
   no │ → emite abstención cordial (sin tarjeta) ─────► END          │
   sí ▼  proposed_action = {summary, params{ids resueltos}}          │
 confirm_appointment                                                 │
        │  decision = interrupt(proposed_action)  ⏸  → /chat emite    │
        │            evento SSE `confirm` (tarjeta + thread_id); pausa│
        │  ── usuario: Confirmar/Cancelar → POST /chat/resume ──┐     │
        ▼  Command(resume="confirm"|"cancel")  ◄───────────────┘     │
   confirm │ → db.create_appointment(**params)  → recibo "✅ …"      │
   cancel  │ → "Cancelado, no creé el turno."                        │
        ▼                                                            │
       END  ◄──────────────────────────────────────────────────────┘
```

Reglas del flujo:
- **One-shot**: la propuesta no pregunta de a uno los campos (eso es slot-filling, diferido). Si falta
  un dato → abstención fail-closed.
- **Cualquier excepción** en extracción/resolución → abstención (no se abre tarjeta, no se escribe).
- **El resolver nunca deja que el LLM toque UUIDs**: el LLM da nombres/strings; los IDs salen de la DB
  scopeada por `practice_id`. Si el LLM "inventa" un cliente que no está en la DB → 0 matches →
  abstención (no se crea nada para un cliente fantasma).
- **El recibo y la cancelación son texto determinístico** → el `/chat/resume` **no necesita Ollama**
  (funciona aun con el LLM caído).

## Agente — `app/agents/action_agent.py`

```python
class ProposedAppointment(BaseModel):          # structured output del extractor (args tipados chicos)
    client_name: str
    practitioner_name: str | None = None
    start_at: str                              # ISO 8601 absoluto, computado por el LLM desde `now`
    duration_min: int = 30
    reason: str | None = None
    channel: Literal["presencial", "telellamada"] | None = None

@dataclass
class ProposalResult:
    proposed_action: dict | None               # {kind, summary, params{...ids resueltos...}} o None
    abstained: bool
    message: str                               # mensaje cordial al usuario si abstuvo (o "")
    reason: str                                # diagnóstico interno (log), no se muestra crudo

async def propose_appointment(
    question: str, practice_id: str, *, now: datetime, pool=None, gen_llm=None,
) -> ProposalResult
```

### Extracción (`gemma4:12b`, `with_structured_output(ProposedAppointment)`)
- Args **tipados chicos** (str/int/enum) → `with_structured_output` **sí** funciona en Gemma local
  (lo usan hoy el router `RouterDecision` y el juez `SqlIntentVerdict`). Esto contrasta con la
  generación **texto-libre** de SQL del Slice 3, que devolvía `None` por esa vía. Si en el e2e
  resultara frágil, fallback a `format=` JSON Schema de Ollama (mismo principio, decodificación
  restringida; CLAUDE.md §4) — anotado, no esperado.
- Prompt (a mano, español): rol = asistente de agenda; se le pasa el **`now` en ISO** y se le pide
  computar `start_at` **absoluto** ("mañana 10:00" → ISO con la fecha real); `duration_min` por defecto
  `appt_default_duration_min` si no se menciona; `channel` solo si el usuario lo dice.

### Resolver determinístico (sin LLM, scoped por `practice_id`)
1. **Cliente**: `db.find_clients_by_name(practice_id, client_name)` (ILIKE, cap `appt_name_match_limit`).
   `0` → abstención *"No encontré ningún cliente que coincida con «X». ¿Me das el nombre completo?"*;
   `>1` → *"Hay varios que coinciden con «X»: A, B, C. ¿Cuál?"* (lista los candidatos); `1` → `client_id`.
2. **Profesional**: si `practitioner_name` dado → `find_practitioners_by_name` (misma lógica 0/>1/1).
   Si `None` → `list_active_practitioners`: **exactamente 1 activo** → default ese; `>1` → abstención
   *"¿Con qué profesional? Tenés: A, B, C."*; `0` → abstención *"No hay profesionales activos cargados."*.
3. **Fecha/hora**: `datetime.fromisoformat(start_at)` (Python 3.11+, sin dep nueva). Si **falla el
   parseo** → abstención *"No entendí la fecha/hora. ¿Me la indicás? (p. ej. 'mañana 10:00')"*. Si
   viene **naive** (sin offset), se asume **UTC** (al LLM se le pasó `now` en UTC). **No** se valida
   pasado/futuro en código: la tarjeta HITL es el control humano de la fecha (alguien puede registrar
   un turno pasado a propósito). `end_at = start_at + timedelta(minutes=duration_min)`.
4. **`proposed_action`** (lo que viaja al `interrupt` y se checkpointea):
   ```python
   {
     "kind": "create_appointment",
     "summary": "Crear turno: Ana López con Dra. Gómez — mar 30/6 10:00–10:30 (30 min), "
                "motivo: control, presencial",        # legible, para la tarjeta
     "params": {                                       # resuelto, listo para el INSERT
       "client_id": "...", "client_name": "Ana López",
       "practitioner_id": "...", "practitioner_name": "Dra. Gómez",
       "start_at": "2026-06-30T10:00:00+00:00", "end_at": "2026-06-30T10:30:00+00:00",
       "reason": "control", "channel": "presencial", "status": "programado",
     },
   }
   ```
   `client_name`/`practitioner_name` se guardan para que la tarjeta y el recibo no re-consulten la DB.

> **PII en el checkpoint**: `proposed_action` (con nombre del paciente) se persiste en el checkpointer
> Postgres **local** — mismo límite de confianza que `appointments`, no sale de la máquina (CLAUDE.md
> §0). La regla "audit sin PII cruda" (§5) aplica al **audit log** (diferido), no al state del grafo
> que el blueprint §3.2 define con `proposed_action` explícitamente.

## Tool parametrizada — `app/db.py`

```python
async def find_clients_by_name(practice_id, name, *, limit) -> list[dict]   # id::text, full_name (ILIKE)
async def list_active_practitioners(practice_id) -> list[dict]              # id::text, full_name (active)
async def find_practitioners_by_name(practice_id, name, *, limit) -> list[dict]
async def create_appointment(
    practice_id, client_id, practitioner_id, start_at, end_at,
    *, reason=None, channel=None, status="programado", created_by=None,
) -> dict                                                                   # fila creada (recibo)
```

`create_appointment` — **INSERT parametrizado con guarda de tenant (defensa en profundidad)**: aunque
el resolver ya scopeó por `practice_id`, el INSERT solo procede si `client_id` **y** `practitioner_id`
pertenecen a ese `practice_id`:

```sql
INSERT INTO appointments (practice_id, client_id, practitioner_id, start_at, end_at, status, reason, channel, created_by)
SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9
WHERE EXISTS (SELECT 1 FROM clients       WHERE id = $2 AND practice_id = $1)
  AND EXISTS (SELECT 1 FROM practitioners WHERE id = $3 AND practice_id = $1)
RETURNING id::text, start_at, end_at, status;
```

Si no devuelve fila (cliente/profesional de otra práctica o inexistente) → `RuntimeError` (no debería
pasar tras el resolver; es el cinturón). `created_by` = `NULL` por ahora (no hay contexto de usuario
autenticado en dev; se completará con auth real, Fase 4). Parámetros `$n` (asyncpg) → **sin
interpolar texto**: es el opuesto del SQL-libre prohibido por §4.

## Nodos del grafo — `app/graph/nodes.py`

```python
async def propose_appointment_node(state: AgentState) -> dict:
    now = datetime.now(UTC)
    result = await propose_appointment(last_user_text(state), state["practice_id"], now=now)
    if result.abstained:
        write_token(result.message); write_sources([])
        return {"proposed_action": None, "sources": [],
                "messages": [AIMessage(content=result.message)]}
    return {"proposed_action": result.proposed_action}      # SIN emitir: la tarjeta sale del interrupt

async def confirm_appointment_node(state: AgentState) -> dict:
    action = state["proposed_action"]
    decision = interrupt(action)                            # 1ª corrida: pausa; resume: devuelve "confirm"/"cancel"
    if decision == "confirm":
        row = await create_appointment(state["practice_id"], **action["params"])
        msg = _format_receipt(action["params"], row)        # "✅ Turno creado: …" (determinístico)
    else:
        msg = "Cancelado, no creé el turno."
    write_token(msg); write_sources([])
    return {"sources": [], "messages": [AIMessage(content=msg)]}
```

- `propose_appointment_node` **no** emite en el camino feliz: la tarjeta la emite el transporte a
  partir del valor del `interrupt` (abajo). En el camino de abstención sí emite el mensaje y termina.
- `_format_receipt` arma el recibo desde `params` + la fila (id/estado) — sin LLM.

### `edges.py` / `build.py`
- `_INTENT_TO_NODE["action"] = "propose_appointment"` (era `action_stub`).
- `route_after_propose(state) -> "confirm_appointment" | END`: `END` si `proposed_action is None`
  (abstuvo), si no `"confirm_appointment"`.
- `build.py`: registra `propose_appointment` y `confirm_appointment`, quita `action_stub`; agrega el
  conditional edge `propose_appointment → {confirm_appointment, END}` y `confirm_appointment → END`.

### `state.py`
`AgentState` suma `proposed_action: dict | None` (campo ya previsto en el blueprint §3.2). `new_state`
lo inicializa en `None`. Se mantiene el state mínimo (CLAUDE.md §4).

## Transporte HTTP — `app/main.py`

### Surface del `interrupt` por SSE (en `/chat`)
El loop actual solo lee `stream_mode="custom"`. Para que la tarjeta llegue al front, `/chat` pasa a
`stream_mode=["custom", "updates"]` y maneja tuplas `(mode, chunk)`:
- `mode == "custom"` → `token` / `sources` como hoy.
- `mode == "updates"` y `chunk` trae `__interrupt__` → emitir **evento SSE `confirm`** con
  `{"thread_id": <id>, "action": <Interrupt.value = proposed_action>}`.
- al final, `done`.

> **Spike (riesgo #1)**: confirmar la forma exacta del `__interrupt__` en `astream` multi-mode (clave/
> shape del `Interrupt`). Alternativa equivalente si molesta: tras el `astream`, `graph.aget_state(
> config)` y leer `state.interrupts`/`.next`. Se elige una en el plan tras un spike de 10 min.

El `thread_id` ya existe en `new_state(...)`; hoy se mintea por request y **no se devolvía** — ahora
viaja en el evento `confirm` para que el front pueda reanudar **ese** thread.

### Nuevo endpoint de reanudación
```python
class ResumeRequest(BaseModel):
    thread_id: str
    decision: Literal["confirm", "cancel"]

@app.post("/chat/resume")
async def chat_resume(req: ResumeRequest, request: Request) -> EventSourceResponse:
    graph = getattr(request.app.state, "graph", None) or get_default_graph()
    config = {"configurable": {"thread_id": req.thread_id}}
    # reusa el mismo event_stream (factorizado) sobre graph.astream(Command(resume=req.decision), config)
```
- **No** hace probe de Ollama (el resume es determinístico). Reusa el helper de streaming de `/chat`.
- Si el `thread_id` no tiene un interrupt pendiente (doble-submit, expiró), el `astream` no produce y
  cae a `done` → el front ya no muestra la tarjeta; aceptable (idempotencia básica).

## Frontend (mínimo funcional, sobre `useLocalRuntime`)

> **Esta es la parte más fiddly (riesgo #2).** El `ChatModelAdapter` de assistant-ui es un *stream de
> texto*; una tarjeta interactiva a mitad de conversación no calza en un adapter de texto puro. Enfoque:

- **`chatStream.ts`**: `ChatEvent` suma `{ type: "confirm"; threadId: string; action: ProposedAction }`
  (parse del evento SSE `confirm`); nueva `async function* resumeChat(threadId, decision)` que pega a
  `/api/chat/resume` y streamea `token`/`done` igual que `streamChat`.
- **`runtime.ts`**: cuando el adapter recibe `confirm`, **no** intenta renderear botones dentro del
  mensaje; llama a un callback `onConfirmRequested(threadId, action)` (inyectado vía closure/estado) y
  cierra el turno con un texto breve ("Esperando tu confirmación…"). 
- **`page.tsx`**: tiene estado `pendingAction: {threadId, action} | null`. Cuando se setea, renderiza
  `<ConfirmCard>` **fuera del `<Thread>`** (debajo del chat). Al **Confirmar/Cancelar**: llama
  `resumeChat`, **empuja el recibo al hilo** (vía `runtime` / append de un mensaje assistant) y limpia
  `pendingAction`.
- **`ConfirmCard.tsx`**: muestra `action.summary` (+ campos clave) y dos botones; deshabilita al click
  (evita doble-submit). Sin dependencias nuevas.
- El rewrite `/api/* → :8000` de `next.config.mjs` ya cubre `/api/chat/resume`.

> El plan validará el mecanismo exacto de "empujar el recibo al hilo" con `useLocalRuntime` (append vs.
> segundo `run`); si resultara incómodo, el fallback aceptable es renderear el recibo dentro de la
> `ConfirmCard` (igual cumple el smoke). La migración a `@assistant-ui/react-ui` sigue diferida.

## Config nueva (`config.py`)

| Var | Default | Para qué |
|---|---|---|
| `appt_default_duration_min` | `30` | duración del turno si el usuario no la dice |
| `appt_name_match_limit` | `5` | tope de candidatos en la resolución de nombres (ILIKE) |

Modelos: extracción con `ollama_model` (`gemma4:12b`); **sin LLM** en resolver/escritura/recibo. Todo
local por Ollama.

## Multi-tenant (CLAUDE.md §0.5)

`practice_id` viaja en `AgentState` (de `settings`, single-tenant en dev). El **resolver** consulta
`clients`/`practitioners` filtrando por `practice_id`; el **INSERT** re-verifica con `EXISTS(... AND
practice_id = $1)`. No hay forma de crear un turno para un cliente/profesional de otra práctica.
Pre-RLS el aislamiento es **app-level** (resolver scopeado + guarda en el INSERT); RLS en Postgres es
el cierre real (Fase 4, consistente con el Slice 3).

## Seguridad / guardrails

- **HITL inquebrantable**: sin `interrupt` → confirmación explícita, **no hay escritura** (gate §2/§6).
  El camino de abstención y el de "cancelar" no escriben.
- **Tool parametrizada, no SQL libre** (§4): `create_appointment` usa parámetros `$n`; el LLM nunca
  emite SQL ni toca IDs.
- **PII**: la tarjeta y el recibo muestran el nombre del paciente al usuario **autorizado**; los logs
  registran `kind` + ids + decisión, **no** nombres en crudo (mismo criterio del Slice 3). El state se
  persiste en Postgres local (ver nota PII arriba).
- **Inyección**: la frase del usuario nunca se concatena a SQL; el LLM solo produce args tipados que
  el resolver valida contra la DB. Presidio (entrada) y detección de inyección son su propio slice de
  guardrails (Fase 1 posterior); este slice no los agrega.

## Testing (DoD CLAUDE.md §6)

Patrón establecido: inyección de `pool=`/`gen_llm=` y `monkeypatch` de funciones de módulo
(`tests/test_router.py`, `tests/test_nodes.py`, `tests/test_sql_agent.py`).

- **No-llm** (sin Ollama):
  - `test_action_agent.py`: resolver con `pool` fake / `db.*` monkeypatcheado y `gen_llm` fake (estilo
    `FakeRouterLLM` devolviendo un `ProposedAppointment`): cliente **1** → `proposed_action` con ids;
    cliente **0** → abstención "no encontré"; cliente **>1** → abstención "varios"; profesional `None`
    con **1 activo** → default; con **>1** → abstención "¿con qué profesional?"; fecha inválida →
    abstención; `end_at = start + duration`; `summary` legible.
  - `test_create_appointment.py` (integración, DB real local): inserta y devuelve fila; **rechaza**
    (0 filas → `RuntimeError`) cliente/profesional de **otra** `practice_id` (guarda de tenant).
  - `test_nodes.py` (extender): `propose_appointment_node` feliz devuelve `proposed_action` y **no
    emite**; abstención emite el mensaje + `write_sources([])`.
  - **`test_hitl_cycle.py`** (el central, **sin Ollama**): grafo con `MemorySaver`, `propose_appointment`
    monkeypatcheado a un `ProposalResult` ya resuelto (sin LLM); `graph.ainvoke(state, config)` →
    aserta que **se interrumpió** con `proposed_action` en el `__interrupt__`; `graph.ainvoke(Command(
    resume="confirm"), config)` con `create_appointment` espiado → **se llamó una vez** con los params
    correctos y el recibo contiene "✅"; con `resume="cancel"` → `create_appointment` **no** se llamó.
    (Cubre la Decisión #1: el resolver/LLM no recomputa al reanudar.)
  - `test_edges.py` (o extender): `route_after_propose` → `END` si `proposed_action is None`, si no
    `confirm_appointment`; `_INTENT_TO_NODE["action"] == "propose_appointment"`.
- **`-m llm`** (`test_action_e2e_llm.py`; Ollama + Postgres reales, `seed_demo.py` corrido): tomar un
  `full_name` real de un cliente del seed; *"agendá un turno para \<ese nombre\> mañana a las 10"* →
  ciclo `ainvoke` → interrupt con `proposed_action`; `resume="confirm"` → la fila existe en
  `appointments` (`COUNT` antes/después +1, scoped por `practice_id`); camino `cancel` → no incrementa.
- **Frontend** (`vitest`): `chatStream.test.ts` parsea el evento `confirm`; `ConfirmCard.test.tsx`
  rinde `summary` y dispara `onConfirm`/`onCancel`; (si factible) test del wiring `pendingAction`.
- **Gates**: `ruff check . && ruff format .`; `mypy --config-file backend/pyproject.toml` (siempre con
  la config: sin ella, falso-positivo `asyncpg [import-untyped]`); `pytest -q` (no-llm) verde. **Smoke
  §2**: *"agendá un turno para \<cliente\> mañana 10:00"* → **abre tarjeta** → Confirmar → ✅ + fila;
  Cancelar → nada escrito. (Reemplaza la línea del smoke que veía el stub.)

## Dependencias

Ninguna nueva. `langgraph` ya provee `interrupt` y `Command` (checkpointer Postgres ya cableado en el
`lifespan`). El parseo de fecha usa `datetime.fromisoformat` (stdlib, Python 3.11+). Frontend sin deps
nuevas. Sin red saliente fuera de Ollama/Postgres/Qdrant locales (DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff`, `mypy --config-file backend/pyproject.toml`, `pytest -q` (no-llm) verdes; `-m llm` verde con
   Ollama + `gemma4:12b` + Postgres + `seed_demo.py` corrido.
2. Tocamos el grafo y agregamos una tool de escritura: el smoke de §2 pasa y, sobre todo, **las
   escrituras piden confirmación de verdad** (ya no es stub). El ciclo interrupt→resume tiene test
   no-llm (`test_hitl_cycle.py`).
3. No se tocó retrieval/SQL/síntesis/router-clasificación → la suite offline de eval no aplica acá
   (el router ya rutea "agendá…" a `action`; si el e2e mostrara un fallo de ruteo, se agrega un caso).
4. Prompt de extracción escrito a mano ahora; recompilar con DSPy queda anotado para Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales.
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **Recompute/re-alucinación al reanudar** (el riesgo de fondo del HITL): mitigado por la **Decisión
  #1** (dos nodos; la propuesta se checkpointea y no se regenera). Cubierto por `test_hitl_cycle.py`.
- **Surface del `interrupt` por SSE** (riesgo #1): spike corto de la forma del `__interrupt__` en
  `astream` multi-mode; fallback `aget_state`. Decidido en el plan.
- **Tarjeta interactiva con `useLocalRuntime`** (riesgo #2): la parte más fiddly; enfoque `pendingAction`
  fuera del `<Thread>`; fallback = recibo dentro de la `ConfirmCard`. Decidido en el plan.
- **`with_structured_output` frágil para los args en Gemma local**: bajo riesgo (args tipados chicos ya
  funcionan en router/juez; lo que fallaba era texto-libre SQL). Fallback: `format=` JSON Schema de Ollama.
- **El LLM computa mal la fecha relativa** ("mañana"): la **tarjeta HITL** es la mitigación — el humano
  ve la fecha resuelta y cancela si está mal. tz/DST por práctica = endurecimiento posterior (MVP UTC).
- **Resolución de nombres por ILIKE** (homónimos, acentos): fail-closed en ambigüedad (lista candidatos
  y pide precisión). Matching más fino (unaccent/trigram) = endurecimiento posterior.
- **Checkpoints huérfanos** (usuario abre tarjeta y nunca confirma): no escriben nada; acumulan estado
  en Postgres. TTL/limpieza de threads = tarea de mantenimiento posterior, no bloquea.
- **Aislamiento tenant pre-RLS**: app-level (resolver scopeado + guarda EXISTS en el INSERT); RLS en
  Fase 4 es el cierre real.
