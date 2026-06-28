# Praxia · Fase 1 · Slice 5 — Tool de escritura `log_interaction` + registry de dispatch

> Diseño aprobado el 2026-06-28. Spec de un único slice implementable.
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md`.
> Slices previos: grafo + router (`2026-06-25-grafo-router-design.md`),
> subgrafo CRAG (`2026-06-26-crag-design.md`),
> Data Agent NL2SQL read-only (`2026-06-26-nl2sql-data-agent-design.md`),
> write-tool `create_appointment` con HITL (`2026-06-27-write-appointment-hitl-design.md`).

## Objetivo

Agregar la **segunda tool de escritura** de Praxia — `log_interaction` (registrar una interacción con
un cliente: sesión, llamada, email, nota, mensaje) — **reusando el esqueleto HITL** del Slice 4
(`interrupt` + tarjeta de confirmación, CLAUDE.md §4). Y, al hacerlo, **resolver el dispatch entre
múltiples write-tools de la forma correcta**: introducir un **registry de write-tools** con nodos
`propose_action` / `confirm_action` **genéricos** que despachan por `kind`, en vez de hardcodear una
tool. La tabla `interactions` (Blueprint §5.2, "el corazón del CRM"; hoy **no** existe en `schema.sql`)
se porta en este slice.

Entregable observable:

- *"registrá que llamé a \<cliente\> y confirmó el turno del martes"* → el grafo **clasifica** la acción
  como `log_interaction`, **resuelve** el cliente (nombre→UUID, scoped por `practice_id`) y **se pausa**
  mostrando la **misma tarjeta** *"Registrar llamada de Ana López — «confirmó el turno del martes».
  ¿Confirmás?"* con **Confirmar / Cancelar**.
- **Confirmar** → se escribe la fila en `interactions` (tool parametrizada) → recibo *"✅ Interacción
  registrada: llamada de Ana López (12/03 14:30 UTC)."*.
- **Cancelar** → no se escribe nada → *"Cancelado, no registré la interacción."*.
- *"agendá un turno para \<cliente\> mañana 10:00"* → **sigue funcionando igual** (clasifica
  `create_appointment`, misma tarjeta y escritura del Slice 4): el registry no rompe la 1ª tool.
- Pedido de acción que **aún no es tool** (*"cancelá el turno de Juan"*) → el clasificador devuelve
  `unsupported` → **abstención** cordial, sin tarjeta, sin escribir.
- Datos no resolubles con seguridad (cliente inexistente/ambiguo, frase sin cliente) → **abstención
  fail-closed**, sin tarjeta, sin escribir (idéntico al Slice 4).

Gate que cierra el slice (CLAUDE.md §2/§6): el smoke registra una interacción **solo tras confirmar**,
turnos sigue verde (no-regresión del refactor), y **ninguna escritura ocurre sin confirmación**.

## No-objetivos (diferidos, cada uno es trabajo propio posterior)

- **Vincular la interacción a `practitioner_id` / `appointment_id`** (columnas que la tabla §5.2 sí
  tiene): el MVP llena la firma de Blueprint §6 — `(client_id, type, summary, content)` — y deja esas
  dos FKs en `NULL`. Resolver el profesional/turno de la frase es alcance posterior.
- **Extracción NL de `occurred_at`** ("ayer", "el lunes pasado"): el MVP usa `now()` (UTC). Parsear
  fechas relativas para la fecha de ocurrencia = endurecimiento posterior (igual que tz por práctica
  quedó diferido en el Slice 4).
- **Redacción PII de `content`** (Presidio, español): `content` puede traer datos de salud; este slice
  lo almacena como se almacenan hoy `clients.notes` / `appointments.reason`. La redacción en
  entrada/salida del grafo es **su propio slice de Guardrails** (CLAUDE.md §5).
- **Slot-filling multi-turno**: la propuesta es **one-shot** (si falta cliente/tipo, abstiene). Pedir
  los campos faltantes de a uno y recordar la conversación requiere `thread_id` estable + memoria → su
  propio slice.
- **`agent_runs` / audit log + `consents`** (CLAUDE.md §5, Blueprint §5.2): siguen sin existir; la fila
  creada (con `created_at`) es el registro por ahora. Audit formal y enforcement de consentimiento =
  Fase 4 (consistente con el Slice 4).
- **Servidor MCP `mcp_servers/mcp_postgres.py`**: igual que `create_appointment`, `log_interaction` se
  construye **in-process** (`db.log_interaction` + `agents/interaction_agent.py`); el contrato §4 se
  cumple en espíritu (tool parametrizada + HITL, sin SQL del LLM). El wrapper MCP llega cuando lo pida
  el dev loop (§8) o prod (§7 "no construyas de más").
- **`update_interaction` / `delete_interaction`** y reprogramar/cancelar turnos: este slice solo
  **crea** interacciones. El clasificador los reconoce como `unsupported` hasta que existan.
- **Compilar el prompt del clasificador con DSPy**: a mano ahora; recompilar = Fase 2.
- **Canvas rico / append del recibo al hilo**: la `ConfirmCard` actual (recibo dentro de la tarjeta)
  se reusa **tal cual**; el canvas sigue diferido en Fase 1.

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). Inferencia 100% local por Ollama: el
clasificador (`gemma4:e4b`) elige la tool; `gemma4:12b` extrae los argumentos estructurados; **no hay
LLM en el resolver ni en la escritura** (determinísticos). El grafo es la fuente de control: la tool se
enchufa detrás del router (intent `action`); ningún camino esquiva router/guardrails. **Lectura y
escritura separadas por diseño**: la escritura nunca es SQL libre — es una tool parametrizada y
**siempre** detrás del `interrupt` de confirmación. Aislamiento por `practice_id` en la resolución y en
el INSERT (CLAUDE.md §0.5).

## Arquitectura

### Decisión de límites #1 — Registry de write-tools, no router fino (la del dispatch)

Con una segunda tool de escritura hay que decidir **quién** elige la tool. Tres caminos posibles:
(A) un **registry** + nodos `propose_action`/`confirm_action` genéricos que despachan por `kind`;
(B) que el **router** emita intents finos (`action_appointment`/`action_interaction`); (C) un nodo
**`classify_action`** dedicado entre router y propose.

Se elige **(A) registry**. Razones:

- **El router queda grueso y estable.** El prompt del router (`gemma4:e4b`, el modelo más débil) **no**
  crece con cada write-tool; sigue clasificando solo el dominio (`rag|sql|action|chitchat|out_of_scope`).
  La taxonomía de tools de escritura **no se filtra** al router. (B) la metería ahí y la haría crecer
  con cada tool; (C) es equivalente a (A) pero con un nodo extra.
- **El `kind` ya es la junta.** `proposed_action` ya lleva `{"kind", "summary", "params"}` (Slice 4);
  el `confirm` ya recibe el dict entero por el `interrupt`. Generalizar el dispatch es completar una
  costura que ya existe, no inventar una.
- **Escala a N tools sin tocar el grafo.** Agregar la 3ª/4ª tool = registrar un descriptor; los nodos,
  edges, el transporte SSE y la `ConfirmCard` no cambian.

### Decisión de límites #2 — DOS nodos (se mantiene la del Slice 4)

`interrupt()` re-ejecuta el nodo entero al reanudar. Por eso la propuesta (clasificación + extracción
LLM + resolución, **no determinística** en su parte LLM) vive en `propose_action` (se **checkpointea**),
y el `interrupt` vive en `confirm_action`. Al reanudar solo re-corre `confirm_action` → **se escribe
exactamente lo confirmado**, sin re-clasificar ni re-extraer. Esta decisión del Slice 4 sigue intacta:
el registry no la altera, la **generaliza**.

### Decisión de límites #3 — clasificar-luego-proponer dentro de `propose_action`

`propose_action` hace dos pasos (ambos funciones puras en `agents/`, testeables por separado):

1. **`classify_write_action(question)`** (`gemma4:e4b`, `with_structured_output`): elige el `kind`
   ∈ `{"create_appointment", "log_interaction", "unsupported"}`. `unsupported` es el **escape hatch**
   para acciones que aún no son tools (cancelar/editar) → abstención amable, sin forzar un `kind`
   equivocado (fail-closed).
2. **`REGISTRY[kind].propose(question, practice_id, now=...)`** (`gemma4:12b` para la extracción +
   resolver determinístico): devuelve `ProposalResult` (igual contrato del Slice 4).

No se pliega la elección de tool en una sola extracción estructurada porque **cada tool tiene un
esquema de args distinto** (turno vs. interacción); un clasificador `e4b` barato y un extractor `12b`
tool-específico es más robusto y más fácil de testear que una unión discriminada.

### Decisión de límites #4 — un archivo por tool, registry fino

```
backend/app/
├── schema.sql                    # +tabla interactions (§5.2 tal cual, idempotente).
├── db.py                         # +log_interaction(...) (INSERT parametrizado con guarda EXISTS).
├── agents/
│   ├── write_tools.py            # NUEVO: WriteTool descriptor + REGISTRY + classify_write_action.
│   ├── resolvers.py              # NUEVO (chico): resolve_single_client(...) compartido (DRY).
│   ├── action_agent.py           # refactor: propose_appointment se expone como descriptor (sin cambio
│   │                             #  de comportamiento); usa resolve_single_client.
│   └── interaction_agent.py      # NUEVO: ProposedInteraction + propose_interaction (extrae + resuelve).
└── graph/
    ├── state.py                  # SIN CAMBIOS: proposed_action: dict | None ya existe.
    ├── nodes.py                  # propose_appointment_node→propose_action_node;
    │                             #  confirm_appointment_node→confirm_action_node (despacho por kind).
    ├── edges.py                  # "action" → "propose_action"; route_after_propose → "confirm_action".
    └── build.py                  # registra propose_action/confirm_action (renombra los 2 nodos).
```

- **Transporte (`main.py`) y frontend NO cambian** (ver §Transporte y §Frontend): el evento SSE
  `confirm` ya emite el `proposed_action` entero, y la `ConfirmCard` ya rinde `action.summary` + recibo
  agnóstica al `kind`. Es el dividendo de haber dejado el HITL genérico en el Slice 4.
- Regla CLAUDE.md §3: un nodo = una función pura/testeable; la lógica de negocio (clasificación,
  extracción, resolución, registry) vive en `agents/`; los nodos solo orquestan (streaming, `interrupt`,
  llamada a la tool).

## Flujo de datos

```
 "registrá que llamé a Ana y confirmó el turno"      (practice_id, now=UTC)
        │
        ▼   router (intent = action)            ← prompt del router SIN cambios
 propose_action ─────────────────────────────────────────────────────────┐
        │  1) classify_write_action  (e4b, structured)                     │  (se checkpointea entero;
        │       kind ∈ {create_appointment, log_interaction, unsupported}  │   no recomputa al reanudar)
        │  2a) kind == unsupported → abstención cordial ─────► END         │
        │  2b) REGISTRY[kind].propose(question, practice_id, now):         │
        │        log_interaction → 12b structured → ProposedInteraction    │
        │          {client_name, type, summary, content}                   │
        │        resolver determinístico (sin LLM, scoped practice_id):    │
        │          client_name → client_id (0→no encontrado, >1→ambiguo)   │
        │        occurred_at = now ; source = 'agente'                     │
        ▼                                                                  │
   ¿resolvió?                                                              │
   no │ → abstención cordial (sin tarjeta) ─────────────────────► END     │
   sí ▼  proposed_action = {kind, summary(card), params{...}}             │
 confirm_action                                                           │
        │  decision = interrupt(proposed_action)  ⏸  → /chat emite evento │
        │            SSE `confirm` (tarjeta + thread_id); pausa            │
        │  ── usuario: Confirmar/Cancelar → POST /chat/resume ──┐          │
        ▼  Command(resume="confirm"|"cancel")  ◄───────────────┘          │
   confirm │ → REGISTRY[kind].write(practice_id, params) → recibo "✅ …"  │
   cancel  │ → REGISTRY[kind].cancel_message                              │
        ▼                                                                 │
       END  ◄─────────────────────────────────────────────────────────────┘
```

Reglas del flujo (heredadas del Slice 4, ahora genéricas):
- **One-shot**: la propuesta no pregunta de a uno los campos faltantes (slot-filling diferido). Falta
  un dato → abstención fail-closed.
- **Cualquier excepción** en clasificación/extracción/resolución → abstención (no se abre tarjeta, no
  se escribe).
- **El resolver nunca deja que el LLM toque UUIDs**: el LLM da nombres/strings; los IDs salen de la DB
  scopeada por `practice_id`.
- **El recibo y la cancelación son texto determinístico** → `/chat/resume` **no necesita Ollama**.

## Registry — `app/agents/write_tools.py`

```python
@dataclass(frozen=True)
class WriteTool:
    kind: str                                   # "create_appointment" | "log_interaction"
    propose: Callable[..., Awaitable[ProposalResult]]   # (question, practice_id, *, now, ...) -> ProposalResult
    write:   Callable[..., Awaitable[dict]]              # (practice_id, params) -> fila creada
    format_receipt: Callable[[dict, dict], str]         # (params, row) -> "✅ …" (determinístico)
    cancel_message: str

REGISTRY: dict[str, WriteTool] = {
    "create_appointment": WriteTool(
        kind="create_appointment",
        propose=propose_appointment,                # de action_agent.py (refactor; mismo comportamiento)
        write=_write_appointment,                   # adapter: params → kwargs de db.create_appointment
        format_receipt=format_appointment_receipt,  # el _format_receipt de hoy, movido acá
        cancel_message="Cancelado, no creé el turno.",
    ),
    "log_interaction": WriteTool(
        kind="log_interaction",
        propose=propose_interaction,                # de interaction_agent.py (nuevo)
        write=_write_interaction,                   # adapter: params → kwargs de db.log_interaction
        format_receipt=format_interaction_receipt,
        cancel_message="Cancelado, no registré la interacción.",
    ),
}

class WriteActionDecision(BaseModel):
    kind: Literal["create_appointment", "log_interaction", "unsupported"]

CLASSIFY_PROMPT = (
    "Sos el despachador de acciones de escritura de un CRM de prácticas profesionales. "
    "El usuario pidió ejecutar UNA acción que modifica datos. Clasificá QUÉ acción es:\n"
    "- create_appointment: agendar/crear un turno o cita. Ej: 'agendá un turno para Ana mañana 10', "
    "'dale una cita a Juan el martes'.\n"
    "- log_interaction: registrar/anotar una interacción ya ocurrida con un cliente (sesión, llamada, "
    "email, nota, mensaje). Ej: 'registrá que llamé a Ana', 'anotá una nota sobre Juan', "
    "'guardá que mandé un email a Pedro'.\n"
    "- unsupported: cualquier otra acción de escritura que NO sea esas dos (cancelar/editar/reprogramar "
    "un turno, dar de baja un cliente, facturar). Ej: 'cancelá el turno de Juan', 'editá la cita'.\n"
    "Respondé solo con la opción."
)

async def classify_write_action(question: str, llm: Any = None) -> str:
    llm = llm or make_llm("gemma4:e4b", temperature=0.0)
    structured = llm.with_structured_output(WriteActionDecision)
    decision = await structured.ainvoke([("system", CLASSIFY_PROMPT), ("human", question)])
    return decision.kind     # fail-closed: si la llamada falla, el nodo captura y abstiene
```

- El `write` por tool es un **adapter** que **mapea** `params` (el dict del `proposed_action`) a los
  kwargs del writer: convierte las fechas ISO-string de vuelta a `datetime`
  (`datetime.fromisoformat`) y **descarta** las claves *display-only* (`client_name`/
  `practitioner_name`, que viven en `params` para la tarjeta/recibo pero no son columnas). Es
  exactamente lo que hace hoy `confirm_appointment_node` (nodes.py:138-147), ahora encapsulado por
  tool → mantiene `confirm_action_node` agnóstico al esquema de cada writer. (No es `**params` directo:
  `create_appointment` no acepta `client_name`.)
- Misma decodificación restringida que el router (`with_structured_output` sobre un enum chico ya
  funciona en Gemma local; lo prueban `RouterDecision` y `SqlIntentVerdict`).

## Agente — `app/agents/interaction_agent.py`

```python
class ProposedInteraction(BaseModel):                 # structured output del extractor (args tipados)
    client_name: str
    type: Literal["sesion", "llamada", "email", "nota", "mensaje"] = "nota"
    summary: str                                      # resumen corto del agente (col. interactions.summary)
    content: str                                      # texto/nota del usuario (col. interactions.content)

async def propose_interaction(
    question: str, practice_id: str, *, now: datetime, gen_llm=None,
) -> ProposalResult                                   # misma firma que propose_appointment (sin pool;
                                                      # usa db.* a nivel módulo, monkeypatcheable en tests)
```

### Extracción (`gemma4:12b`, `with_structured_output(ProposedInteraction)`)
- **Una sola llamada** produce `summary` **y** `content` (sin round-trip extra). `content` = lo que el
  usuario quiere registrar; `summary` = resumen corto generado por el agente (alineado al Blueprint
  §5.2: `summary` = "resumen generado por el agente"). `type` por defecto `"nota"` si no se infiere.
- Prompt (a mano, español): rol = asistente que registra interacciones; se le pide inferir `type` de la
  acción mencionada ("llamé"→llamada, "mandé un email"→email, "sesión"→sesion), escribir un `summary`
  de una línea y poner en `content` el texto completo de la interacción.

### Resolver determinístico (sin LLM, scoped por `practice_id`)
1. **Cliente** (compartido, `agents/resolvers.py`): `resolve_single_client(practice_id, client_name,
   limit=settings.appt_name_match_limit)` (reusa `db.find_clients_by_name`, ILIKE). `0` → abstención
   *"No encontré ningún cliente que coincida con «X». ¿Me das el nombre completo?"*; `>1` → *"Hay
   varios que coinciden con «X»: A, B, C. ¿Cuál?"*; `1` → `client_id`. (Vacío → *"¿Sobre qué cliente
   es la interacción?"*.)
2. **`occurred_at`** = `now` (UTC). **`source`** = `'agente'`. `practitioner_id`/`appointment_id` no se
   resuelven (NULL en MVP).
3. **`proposed_action`** (viaja al `interrupt`, se checkpointea):
   ```python
   {
     "kind": "log_interaction",
     "summary": "Registrar llamada de Ana López — «confirmó el turno del martes»",   # texto de la card
     "params": {
       "client_id": "...", "client_name": "Ana López",
       "type": "llamada",
       "summary": "Ana confirmó su turno del martes",     # col. interactions.summary
       "content": "Llamé a Ana y confirmó el turno del martes.",
       "occurred_at": "2026-06-28T14:30:00+00:00", "source": "agente",
     },
   }
   ```
   > **Ojo a los dos `summary`**: `proposed_action["summary"]` es el **texto de la tarjeta** (legible,
   > determinístico: `type` + `client_name` + recorte del resumen); `params["summary"]` es la **columna
   > DB** (resumen del agente). El de la card no re-consulta la DB (usa `client_name` ya resuelto).

> **PII en el checkpoint**: `proposed_action` (nombre del cliente + `content`) se persiste en el
> checkpointer Postgres **local** — mismo límite de confianza que `interactions`, no sale de la máquina
> (CLAUDE.md §0). La redacción PII de `content` es el slice de Guardrails; el state del grafo lo define
> el blueprint §3.2 con `proposed_action` explícito.

## Tool parametrizada — `app/db.py`

```python
async def log_interaction(
    practice_id, client_id, *, type, summary, content,
    occurred_at, source="agente",
) -> dict                                              # fila creada (id, occurred_at, type) para el recibo
```

**INSERT parametrizado con guarda de tenant (defensa en profundidad)** — aunque el resolver ya scopeó
por `practice_id`, el INSERT solo procede si `client_id` pertenece a ese `practice_id` (mismo cinturón
que `create_appointment`):

```sql
INSERT INTO interactions (practice_id, client_id, type, summary, content, occurred_at, source)
SELECT $1, $2, $3, $4, $5, $6, $7
WHERE EXISTS (SELECT 1 FROM clients WHERE id = $2 AND practice_id = $1)
RETURNING id::text, occurred_at, type;
```

Si no devuelve fila (cliente de otra práctica o inexistente) → `RuntimeError` (no debería pasar tras el
resolver; es el cinturón). `practitioner_id`/`appointment_id` se omiten → `NULL` por defecto.
`created_at` lo pone la DB (`DEFAULT now()`). Parámetros `$n` (asyncpg), **sin interpolar texto**: el
opuesto del SQL-libre prohibido por §4.

## Esquema — `app/schema.sql`

Se agrega la tabla `interactions` de Blueprint §5.2 **tal cual**, idempotente (el `schema.sql` se aplica
con `CREATE TABLE IF NOT EXISTS` repetible, CLAUDE.md §2):

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

(El `CHECK` de `source` incluye `'agente'`, el valor que usa la tool.) No se toca ninguna otra tabla.

## Nodos del grafo — `app/graph/nodes.py`

```python
async def propose_action_node(state: AgentState) -> dict:
    question = last_user_text(state)
    try:
        kind = await classify_write_action(question)
    except Exception:                                  # fail-closed
        kind = "unsupported"
    if kind == "unsupported" or kind not in REGISTRY:
        msg = ("Por ahora puedo agendar turnos o registrar interacciones. "
               "¿Cuál de las dos necesitás?")
        write_token(msg); write_sources([])
        return {"proposed_action": None, "sources": [], "messages": [AIMessage(content=msg)]}
    result = await REGISTRY[kind].propose(question, state["practice_id"], now=datetime.now(UTC))
    if result.abstained:
        write_token(result.message); write_sources([])
        return {"proposed_action": None, "sources": [], "messages": [AIMessage(content=result.message)]}
    return {"proposed_action": result.proposed_action}     # SIN emitir: la tarjeta sale del interrupt

async def confirm_action_node(state: AgentState) -> dict:
    action = state["proposed_action"] or {}
    tool = REGISTRY[action["kind"]]
    decision = interrupt(action)                           # 1ª corrida: pausa; resume: "confirm"/"cancel"
    if decision == "confirm":
        row = await tool.write(state["practice_id"], action["params"])
        msg = tool.format_receipt(action["params"], row)   # determinístico
    else:
        msg = tool.cancel_message
    write_token(msg); write_sources([])
    return {"sources": [], "messages": [AIMessage(content=msg)]}
```

- `propose_action_node` no emite en el camino feliz (la tarjeta la emite el transporte desde el valor
  del `interrupt`). Abstención y `unsupported` sí emiten y terminan.
- `confirm_action_node` despacha por `action["kind"]` al `write`/`format_receipt`/`cancel_message` del
  registry. Es la generalización del `confirm_appointment_node` actual (que hoy hardcodea
  `create_appointment`, nodes.py:138).

### `edges.py` / `build.py`
- `_INTENT_TO_NODE["action"] = "propose_action"` (era `"propose_appointment"`).
- `route_after_propose(state) -> "confirm_action" | END` (renombra el destino).
- `build.py`: registra `propose_action`/`confirm_action` (renombra los 2 nodos y `_LEAF_NODES`); el
  conditional edge pasa a `propose_action → {confirm_action, END}` y `confirm_action → END`.

### `state.py`
**Sin cambios**: `AgentState.proposed_action: dict | None` ya existe (Slice 4). El `kind` discrimina la
tool dentro del dict.

## Transporte HTTP — `app/main.py`

**Sin cambios.** El evento SSE `confirm` ya emite `{"thread_id", "action": <Interrupt.value =
proposed_action>}` (main.py:90-96) — agnóstico al `kind`. `/chat/resume` (determinístico,
`Command(resume=decision)`) tampoco depende de la tool. Cualquier `proposed_action` con `summary` +
`params` viaja por el transporte existente. (Es la razón de haber dejado el HITL genérico en el Slice 4.)

## Frontend

**Sin cambios funcionales.** `ConfirmCard.tsx` rinde `action.summary` (línea 44) + los tokens del recibo
(línea 56); no referencia campos de turno. `chatStream.ts`/`runtime.ts`/`page.tsx` parsean el evento
`confirm` y reanudan por `thread_id`, agnósticos al `kind`.

- **Opcional (contrato)**: agregar un caso a `ConfirmCard.test.tsx` / `chatStream.test.ts` con un
  `action` de `kind: "log_interaction"` (summary de interacción) para **fijar** que la card es genérica.
  Es lo único que toca el front, y es un test, no producto.

## Config (`config.py`)

**Sin vars nuevas.** La resolución de cliente reusa `appt_name_match_limit` (ya existe; mismo ILIKE).
> Nota: el nombre tiene prefijo `appt_` por su origen; un rename futuro a `name_match_limit` genérico es
> trivial y no bloquea — se deja anotado para no churnear config en este slice.

Modelos: clasificador `gemma4:e4b`; extracción `gemma4:12b`; **sin LLM** en resolver/escritura/recibo.

## Multi-tenant (CLAUDE.md §0.5)

`practice_id` viaja en `AgentState` (de `settings`, single-tenant en dev). El **resolver** consulta
`clients` filtrando por `practice_id`; el **INSERT** re-verifica con `EXISTS(... AND practice_id = $1)`.
No hay forma de registrar una interacción para un cliente de otra práctica. Pre-RLS el aislamiento es
**app-level** (resolver scopeado + guarda EXISTS); RLS en Postgres = Fase 4 (consistente con Slices 3/4).

## Seguridad / guardrails

- **HITL inquebrantable**: sin `interrupt` → confirmación explícita, **no hay escritura**. Abstención,
  `unsupported` y "cancelar" no escriben.
- **Tool parametrizada, no SQL libre** (§4): `log_interaction` usa parámetros `$n`; el LLM solo produce
  args tipados (`type` enum, `summary`/`content` texto) que el resolver/writer validan contra la DB.
- **PII**: la tarjeta/recibo muestran el nombre del cliente al usuario autorizado; los logs registran
  `kind` + ids + decisión, **no** nombres ni `content` en crudo. El state se persiste en Postgres local.
  Redacción PII de `content` = slice de Guardrails (Fase 1 posterior); este slice no la agrega.
- **Inyección**: la frase del usuario nunca se concatena a SQL; el `content` se guarda como dato, no se
  ejecuta. Detección de inyección en entrada = slice de Guardrails.

## Testing (DoD CLAUDE.md §6)

Patrón establecido: inyección de `pool=`/`gen_llm=` y `monkeypatch` de funciones de módulo
(`tests/test_action_agent.py`, `test_nodes.py`, `test_hitl_cycle.py`, `test_create_appointment.py`).

- **No-llm** (sin Ollama):
  - `test_write_tools.py` (nuevo): `classify_write_action` con `gen_llm` fake (estilo `FakeRouterLLM`
    devolviendo `WriteActionDecision`) → cada `kind` para frases representativas + `unsupported`;
    `REGISTRY` tiene ambas tools y `kind`s coherentes; fallo del LLM → el **nodo** abstiene (cubierto en
    `test_nodes.py`).
  - `test_interaction_agent.py` (nuevo): `propose_interaction` con `db.*` monkeypatcheado y `gen_llm`
    fake → cliente **1** → `proposed_action` con `client_id`, `type`, `summary`, `content`,
    `occurred_at`, `source='agente'`; cliente **0** → abstención "no encontré"; **>1** → abstención
    "varios"; `client_name` vacío → abstención; `type` por defecto `'nota'`; `summary` de la card
    legible y distinto de `params["summary"]`.
  - `test_log_interaction.py` (nuevo, integración DB real local): inserta y devuelve fila
    (`occurred_at`, `type`); **rechaza** (0 filas → `RuntimeError`) cliente de **otra** `practice_id`
    (guarda de tenant); `practitioner_id`/`appointment_id` quedan `NULL`.
  - `test_nodes.py` (extender): `propose_action_node` — `unsupported` emite el mensaje de capacidades y
    `proposed_action=None`; kind válido feliz devuelve `proposed_action` y **no emite**; fallo del
    clasificador → abstención. `confirm_action_node` **parametrizado por kind**
    (`create_appointment` y `log_interaction`): confirm → llama `tool.write` una vez con los params y el
    recibo trae "✅"; cancel → `tool.write` **no** se llama y usa el `cancel_message` correcto.
  - `test_hitl_cycle.py` (extender/parametrizar): el ciclo `interrupt`→`resume` del Slice 4 ahora con
    `kind="log_interaction"` además del de turno — `MemorySaver`, `propose_*` monkeypatcheado a un
    `ProposalResult` ya resuelto; `ainvoke` → interrumpe con `proposed_action`; `resume="confirm"` →
    `write` espiado se llamó una vez; `resume="cancel"` → no se llamó. (Fija que el dispatch no
    recomputa al reanudar para **ambas** tools.)
  - `test_edges.py` / `test_build` (extender): `_INTENT_TO_NODE["action"] == "propose_action"`;
    `route_after_propose` → `END` si `proposed_action is None`, si no `"confirm_action"`; el grafo
    compila con los nodos renombrados.
  - **No-regresión de turnos**: los tests existentes de `create_appointment` (agente, writer, ciclo
    HITL) siguen verdes tras el refactor a descriptor (mismo comportamiento, nodos renombrados).
- **`-m llm`** (`test_action_e2e_llm.py`, extender; Ollama + Postgres reales, `seed_demo.py` corrido):
  - tomar un `full_name` real del seed; *"registrá que llamé a \<nombre\> y confirmó el turno"* → ciclo
    `ainvoke` → interrupt con `proposed_action` (`kind=="log_interaction"`); `resume="confirm"` → la
    fila existe en `interactions` (`COUNT` antes/después +1, scoped por `practice_id`); `cancel` → no
    incrementa.
  - **aserto de ruteo/clasificación**: la frase de registro clasifica `log_interaction`; la frase de
    turno (*"agendá un turno para \<nombre\> mañana 10"*) clasifica `create_appointment` y **sigue
    escribiendo en `appointments`** (no-regresión end-to-end).
- **Frontend** (`vitest`): opcional, el caso de contrato `ConfirmCard` con `action` de interacción
  (§Frontend). Verde: vitest + lint + build.
- **Gates**: `ruff check . && ruff format .`; `mypy --config-file backend/pyproject.toml` (siempre con la
  config: sin ella, falso-positivo `asyncpg [import-untyped]`); `pytest -q` (no-llm) verde. **Smoke §2**:
  *"registrá que llamé a \<cliente\>…"* → **abre tarjeta** → Confirmar → ✅ + fila en `interactions`;
  Cancelar → nada. Y *"agendá un turno…"* sigue abriendo tarjeta y escribiendo en `appointments`.

## Dependencias

Ninguna nueva. `langgraph` ya provee `interrupt`/`Command` (checkpointer Postgres cableado en el
`lifespan`). `with_structured_output` ya se usa (router/jueces). Frontend sin deps nuevas. Sin red
saliente fuera de Ollama/Postgres/Qdrant locales (DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff`, `mypy --config-file backend/pyproject.toml`, `pytest -q` (no-llm) verdes; `-m llm` verde con
   Ollama + ambos modelos + Postgres + `seed_demo.py` corrido.
2. Tocamos el grafo y agregamos una tool de escritura: smoke §2 pasa, **las escrituras piden
   confirmación de verdad**, y **turnos no regresiona** (el registry no rompe la 1ª tool). El ciclo
   `interrupt`→`resume` tiene test no-llm para ambos `kind`.
3. No se tocó retrieval/SQL/síntesis ni el **prompt del router** (queda grueso) → la suite offline de
   eval no aplica. El **clasificador de write-actions** sí es prompt nuevo: si el e2e mostrara un fallo
   de clasificación, se agrega un caso (golden) — anotado.
4. Prompts (clasificador + extracción de interacción) escritos a mano ahora; recompilar con DSPy = Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales.
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **Clasificación errónea de la write-action** (registrar vs. agendar vs. unsupported): bajo riesgo —
  los verbos son distintos ("registrá/anotá/guardá" vs. "agendá/cita"); `e4b` con enum chico ya
  clasifica el router. Mitigación: `unsupported` evita forzar un `kind` malo; aserto de ruteo en el
  e2e; caso golden si aparece un fallo. (Análogo al edge conocido del router "¿atienden domingos?".)
- **Regresión del refactor** (renombrar nodos + mover `create_appointment` a descriptor): mitigada por
  mantener los tests de turnos existentes verdes **sin cambiar sus asertos de comportamiento** (solo el
  nombre del nodo). El refactor es mecánico; el comportamiento de turnos no cambia.
- **`with_structured_output` frágil para `ProposedInteraction`**: bajo riesgo (args tipados chicos
  funcionan; `type` es enum). Fallback: `format=` JSON Schema de Ollama (misma decodificación
  restringida, §4) — anotado, no esperado.
- **Calidad del `summary` del agente**: si el resumen sale pobre, la tarjeta igual muestra `type` +
  cliente + recorte; el humano confirma con esa info. Afinar el prompt = Fase 2 (DSPy).
- **`content` con PII de salud sin redacción**: aceptado para este slice (igual que `notes`/`reason`
  hoy); la redacción es el slice de Guardrails. Datos locales, no salen de la máquina (§0).
- **Aislamiento tenant pre-RLS**: app-level (resolver scopeado + guarda EXISTS en el INSERT); RLS en
  Fase 4 es el cierre real.
- **Checkpoints huérfanos** (tarjeta abierta y nunca confirmada): no escriben nada; TTL/limpieza de
  threads = mantenimiento posterior, no bloquea.
