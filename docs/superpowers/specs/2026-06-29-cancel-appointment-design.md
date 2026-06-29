# Praxia · Fase 1 · Slice 6 — Tool de escritura `cancel_appointment` (1ª mutación)

> Diseño aprobado el 2026-06-29. Spec de un único slice implementable.
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md`.
> Slices previos: grafo + router (`2026-06-25-grafo-router-design.md`),
> subgrafo CRAG (`2026-06-26-crag-design.md`),
> Data Agent NL2SQL read-only (`2026-06-26-nl2sql-data-agent-design.md`),
> write-tool `create_appointment` con HITL (`2026-06-27-write-appointment-hitl-design.md`),
> `log_interaction` + registry de write-tools (`2026-06-28-log-interaction-design.md`).

## Objetivo

Agregar la **tercera tool de escritura** de Praxia — `cancel_appointment` (cancelar un turno existente) —
sobre el **registry de write-tools** del Slice 5, sin tocar router, transporte ni front. Es la **primera
tool que MUTA una fila existente**: las dos actuales (`create_appointment`, `log_interaction`) son INSERT
puros que resuelven cliente/profesional por nombre y crean filas. Cancelar exige resolver **cuál** turno,
así que la pieza arquitectónica nueva es un **resolver de turno objetivo** fail-closed, simétrico a
`resolve_single_client` del Slice 5 (`agents/resolvers.py`).

Entregable observable:

- *"cancelá el turno de \<cliente\> del martes"* → el grafo **clasifica** la acción como
  `cancel_appointment`, **resuelve** el cliente (nombre→UUID, scoped por `practice_id`) y luego el **turno
  objetivo** (de los turnos futuros cancelables del cliente, filtrando por la pista de fecha) y **se pausa**
  mostrando la **misma `ConfirmCard`**: *"Cancelar el turno de Ana López con la Dra. Gómez el 01/07 10:00
  (UTC). ¿Confirmás?"* con **Confirmar / Cancelar**.
- **Confirmar** → `UPDATE appointments SET status='cancelado'` (tool parametrizada, guard de tenant) →
  recibo *"✅ Turno cancelado: Ana López con la Dra. Gómez el 01/07 10:00 (UTC)."*.
- **Cancelar** (en la tarjeta) → no se escribe nada → *"Listo, dejé el turno como estaba."*.
- *"agendá un turno…"* y *"registrá que llamé a…"* → **siguen funcionando igual** (el registry no rompe las
  dos tools previas; no-regresión).
- Pedido ambiguo (el cliente tiene varios turnos futuros y la frase no alcanza para desempatar) →
  **abstención fail-closed** listando los candidatos (fecha + hora + profesional), sin tarjeta, sin escribir.
- Datos no resolubles con seguridad (cliente inexistente/ambiguo; cliente sin turnos cancelables; frase sin
  cliente) → **abstención fail-closed**, sin tarjeta, sin escribir (idéntico patrón a Slices 4/5).
- Acción de escritura que **aún no es tool** (*"reprogramá el turno de Juan"*, *"editá la cita"*) → el
  clasificador devuelve `unsupported` → abstención cordial, sin tarjeta, sin escribir.

Gate que cierra el slice (CLAUDE.md §2/§6): el smoke cancela un turno **solo tras confirmar** (la fila pasa
a `status='cancelado'` en la DB), las dos tools previas siguen verdes, y **ninguna escritura ocurre sin
confirmación**.

## No-objetivos (diferidos, cada uno es trabajo propio posterior)

- **`reschedule` / `update_appointment` (reprogramar)**: comparte el resolver de turno de este slice, pero
  agrega parseo de nueva fecha/hora + posible chequeo de solapamiento. Follow-up barato sobre el mismo
  resolver. El clasificador lo reconoce como `unsupported` hasta que exista.
- **`update_client` (editar datos del cliente)**: reusa `resolve_single_client`, pero `notes` es texto
  libre → reintroduce el gap de PII. Su propio slice (idealmente después de Guardrails).
- **Motivo de cancelación / notificación al cliente**: el MVP solo cambia `status` a `'cancelado'`. Guardar
  un motivo (¿en `appointments.reason`? ¿en una `interaction`?) o notificar = trabajo posterior.
- **Slot-filling multi-turno del "¿cuál turno?"**: la propuesta es **one-shot**. Si quedan varios turnos
  candidatos, abstiene listándolos; **no** los pregunta de a uno ni recuerda la conversación. Resolver el
  "¿cuál?" en un turno siguiente requiere `thread_id` estable + memoria de corto plazo → su propio slice.
  Por eso este slice extrae una **pista de fecha opcional** de entrada (decisión de diseño, abajo).
- **Cancelar por identificador que no sea el cliente** ("cancelá mi turno de las 10" sin nombre): el
  resolver parte del cliente (como las otras tools). Fuera de alcance.
- **`agent_runs` / audit log + `consents`**: siguen sin existir; la fila mutada (con su `status` nuevo) es
  el registro por ahora. Audit formal = Fase 4 (consistente con Slices 4/5).
- **Servidor MCP `mcp_servers/mcp_postgres.py`**: como las tools previas, `cancel_appointment` se construye
  **in-process** (`db.cancel_appointment` + `agents/cancel_agent.py`); el contrato §4 se cumple en espíritu
  (tool parametrizada + HITL, sin SQL del LLM). El wrapper MCP llega cuando lo pida el dev loop (§8) o prod.
- **Timezone por práctica**: todo en UTC, etiquetado `(UTC)` en tarjeta/recibo (igual que Slices 4/5).
- **Redacción PII**: este slice **no agrega ni necesita** redacción — `cancel_appointment` no captura texto
  libre nuevo (a diferencia de `log_interaction.content`). La redacción sigue siendo el slice de Guardrails.
- **Compilar prompts con DSPy**: el clasificador y el extractor se escriben a mano ahora; recompilar = Fase 2.

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). Inferencia 100% local por Ollama: el clasificador
(`gemma4:e4b`) elige la tool; `gemma4:12b` extrae los argumentos estructurados; **no hay LLM en el resolver
ni en la escritura** (determinísticos). El grafo es la fuente de control: la tool se enchufa detrás del
router (intent `action`) vía el registry; ningún camino esquiva router/guardrails. **Lectura y escritura
separadas por diseño**: la mutación nunca es SQL libre — es una tool parametrizada (`UPDATE` con `$n`) y
**siempre** detrás del `interrupt` de confirmación. Aislamiento por `practice_id` en la resolución y en el
`UPDATE` (CLAUDE.md §0.5). **Fail-closed**: ante cualquier ambigüedad (cliente o turno), se abstiene y
lista; nunca adivina qué turno cancelar.

## Arquitectura

### Decisión de límites #1 — Reusar el registry tal cual (la tool nueva es un descriptor + clasificador)

El Slice 5 dejó el camino de escritura genérico: `propose_action_node` clasifica el `kind` y delega en
`REGISTRY[kind].propose`; `confirm_action_node` hace `interrupt(action)` y delega en
`REGISTRY[kind].write/.format_receipt`. Agregar la 3ª tool = **registrar un `WriteTool`** + **extender el
clasificador** (`CLASSIFY_PROMPT` + `WRITE_KINDS`). El router queda intacto (sigue grueso, 5 intents, su
prompt no crece). Los nodos, edges, transporte SSE y `ConfirmCard` **no cambian** (salvo una línea de copy,
abajo). Este es exactamente el dividendo que el registry prometía.

### Decisión de límites #2 — la pieza nueva: resolver de turno objetivo (mutar ≠ insertar)

Las tools previas resuelven entidades **referenciadas** (cliente, profesional) para un INSERT. Cancelar
resuelve la **fila a mutar**: de los turnos del cliente, ¿cuál? Esto es un resolver nuevo, `agents/resolvers.py
::resolve_single_appointment`, **simétrico a `resolve_single_client`** (fail-closed: 0 / ambiguo → sin
turno + mensaje cordial). Es la única lógica genuinamente nueva del slice; el resto es cableado del registry.

### Decisión de límites #3 — desambiguación con pista de fecha opcional (porque NO hay memoria)

Sin memoria de corto plazo, un "¿cuál turno?" no se puede responder en un turno siguiente. Por eso el
extractor saca **`client_name` + una pista de fecha/hora OPCIONAL** (`when`), y el resolver filtra por ella:

1. Trae los turnos **cancelables** del cliente (futuros, `status ∈ {programado, confirmado}`).
2. Si vino `when`: filtra por **día**; si quedan varios y `when` trae hora explícita (≠ 00:00), refina por
   `hora:minuto`.
3. **Exactamente 1** → propone. **0** o **>1** → **abstiene** listando los candidatos.

Alternativas descartadas: (A) solo nombre → ignora la fecha que el usuario sí dijo, abstiene de más;
(C) elegir el más próximo automáticamente → arriesga proponer el turno equivocado y un humano apurado lo
confirma (va contra el fail-closed del resto del código; la tarjeta HITL no debe ser la única red).

### Decisión de límites #4 — finder vs. writer: dos guards distintos, a propósito

- **Finder** (`find_cancellable_appointments`): define lo que es **ofrecible** → futuro (`start_at >= now`)
  y `status ∈ {programado, confirmado}`. No se ofrece cancelar lo pasado, ya atendido, ausente o cancelado.
- **Writer** (`cancel_appointment`): define lo que es **mutable con seguridad** → guard `practice_id` (tenant)
  + `status ∈ {programado, confirmado}` (idempotencia / TOCTOU: si entre propuesta y confirmación alguien lo
  canceló o atendió, el `UPDATE` matchea 0 filas y el recibo lo informa con cortesía). El writer **no**
  re-chequea `start_at >= now` — no queremos fallar por "confirmaste 2 minutos tarde"; el estado es lo que
  puede cambiar de manera significativa entre propuesta y confirmación.

### Decisión de límites #5 — un archivo por tool (módulo propio `cancel_agent.py`)

```
backend/app/
├── db.py                         # +find_cancellable_appointments(...) (finder con JOIN practitioners)
│                                 # +cancel_appointment(...) (UPDATE parametrizado, guard practice_id+status)
├── agents/
│   ├── resolvers.py              # +AppointmentResolution + resolve_single_appointment(...) (fail-closed)
│   ├── cancel_agent.py           # NUEVO: ProposedCancellation + propose_cancellation (extrae + resuelve)
│   └── write_tools.py            # +_write_cancel + format_cancel_receipt + REGISTRY["cancel_appointment"]
│                                 #  + WRITE_KINDS y CLASSIFY_PROMPT extendidos
└── graph/
    └── nodes.py                  # SOLO copy: mensaje de capacidades de propose_action_node incluye
                                  #  "cancelar turnos"; + cleanup opcional del `or {}` muerto (abajo)
```

- `action_agent.py` (turnos/create) e `interaction_agent.py` quedan **intactos**.
- `ProposalResult` se sigue importando de `action_agent.py` (como hace `interaction_agent`); no se mueve a un
  módulo compartido en este slice (consistencia > churn; un futuro `agents/types.py` es trivial y no bloquea).
- Regla CLAUDE.md §3: un nodo = una función pura/testeable; la lógica nueva (resolver, extractor, adapter)
  vive en `agents/`; los nodos solo orquestan. Los nodos ya son genéricos por kind → no se tocan (salvo copy).

## Flujo de datos

```
 "cancelá el turno de Ana del martes"            (practice_id, now=UTC)
        │
        ▼   router (intent = action)                       ← prompt del router SIN cambios
 propose_action ──────────────────────────────────────────────────────────────┐
        │  1) classify_write_action  (e4b, text-parse)                          │  (se checkpointea entero;
        │       kind ∈ {create_appointment, log_interaction,                    │   no recomputa al reanudar)
        │               cancel_appointment, unsupported}                        │
        │  2a) kind == unsupported → abstención cordial ─────────► END          │
        │  2b) REGISTRY["cancel_appointment"].propose(question, practice_id, now)│
        │        12b structured → ProposedCancellation {client_name, when?}     │
        │        resolve_single_client (scoped practice_id):                    │
        │           nombre → client (0→no encontrado, >1→ambiguo, ''→falta)     │
        │        parse when (si no parsea → None, degrada; la fecha es opcional)│
        │        resolve_single_appointment (scoped practice_id, sin LLM):      │
        │           futuros cancelables del cliente → filtra por when →         │
        │             0 → no encontrado (lista próximos)                        │
        │            >1 → ambiguo (lista candidatos: fecha+hora+profesional)    │
        │             1 → turno objetivo                                        │
        ▼                                                                       │
   ¿resolvió cliente Y turno?                                                   │
   no │ → abstención cordial (sin tarjeta) ──────────────────────────► END     │
   sí ▼  proposed_action = {kind, summary(card), params{appointment_id, …}}    │
 confirm_action                                                                │
        │  decision = interrupt(proposed_action)  ⏸  → /chat emite evento      │
        │            SSE `confirm` (tarjeta + thread_id); pausa                 │
        │  ── usuario: Confirmar/Cancelar → POST /chat/resume ──┐               │
        ▼  Command(resume="confirm"|"cancel")  ◄───────────────┘               │
   confirm │ → REGISTRY[kind].write(practice_id, params):                      │
        │   │     db.cancel_appointment(practice_id, appointment_id)           │
        │   │       UPDATE … status='cancelado' (guard practice_id+status)      │
        │   │     → fila → "✅ Turno cancelado: …"  |  0 filas → "⚠️ ya no …"   │
   cancel  │ → "Listo, dejé el turno como estaba."                             │
        ▼                                                                      │
       END  ◄────────────────────────────────────────────────────────────────┘
```

Reglas del flujo (heredadas de Slices 4/5, ya genéricas):
- **One-shot**: la propuesta no pregunta de a uno; falta de datos o ambigüedad → abstención fail-closed.
- **Cualquier excepción** en clasificación/extracción/resolución → abstención (no abre tarjeta, no escribe).
- **El resolver nunca deja que el LLM toque UUIDs**: el LLM da nombre + pista de fecha; el `appointment_id`
  sale de la DB scopeada por `practice_id` + `client_id`. El writer recibe solo el `appointment_id` resuelto.
- **El recibo y la cancelación de la tarjeta son texto determinístico** → `/chat/resume` no necesita Ollama.

## Resolver — `app/agents/resolvers.py`

```python
@dataclass
class AppointmentResolution:
    appointment: dict[str, Any] | None     # {id, start_at, end_at, status, practitioner_id, practitioner_full_name}
    abstain_message: str
    abstain_reason: str                     # "appointment_none" | "appointment_not_found" | "appointment_ambiguous" | "ok"

async def resolve_single_appointment(
    practice_id: str, client: dict[str, Any], when: datetime | None, *, now: datetime, limit: int,
) -> AppointmentResolution:
    """Resuelve a UN turno cancelable del cliente. Fail-closed: 0 / ambiguo → sin turno + mensaje cordial."""
```

Lógica (sin LLM; `client` ya resuelto por `resolve_single_client`, trae `id` y `full_name`). Nota: `cands`
es la lista **completa** de cancelables (se preserva para listar en los mensajes); `matches` es el subconjunto
tras aplicar la pista de fecha:

1. `cands = await db.find_cancellable_appointments(practice_id, client["id"], now=now, limit=limit)`.
2. Si `not cands` → `AppointmentResolution(None, "{full_name} no tiene turnos próximos para cancelar.",
   "appointment_none")`.
3. `matches = cands`; si `when is not None`:
   - `same_day = [a for a in cands if a["start_at"].date() == when.date()]`
   - si `len(same_day) > 1` y `when.time() != time(0, 0)`:
     `timed = [a for a in same_day if (a["start_at"].hour, a["start_at"].minute) == (when.hour, when.minute)]`;
     `same_day = timed or same_day` (si la hora no matchea ninguno, se cae al día → mejor listar que vaciar).
   - `matches = same_day`
4. `len(matches) == 0` → `appointment_not_found`: *"No encontré un turno de {full_name} para esa fecha. Sus
   próximos turnos: {lista de `cands`}."* (lista la completa, sin el filtro, para ser útil).
5. `len(matches) > 1` → `appointment_ambiguous`: *"{full_name} tiene varios turnos próximos: {lista de
   `matches`}. ¿Cuál? Decime la fecha y la hora."*
6. `len(matches) == 1` → `AppointmentResolution(matches[0], "", "ok")`.

Helper `_format_candidate(a) -> "martes 01/07 10:00 con Dra. Gómez"` (día + fecha + hora + profesional, UTC).
> **Día de la semana**: usar un **mapa fijo español** indexado por `a["start_at"].weekday()`
> (`["lunes","martes",…,"domingo"]`), **no** `strftime("%A")` — el proyecto corre en Windows y `%A` es
> locale-dependiente (en C-locale daría "Tuesday" o fallaría). Mismo criterio para cualquier nombre de día.
> **Granularidad de `when`** (limitación conocida y anotada): se filtra por día y, si hay hora explícita,
> por hora:minuto. Una hora `00:00` se trata como "sin hora" (nadie cancela un turno de medianoche). Es una
> heurística; el camino fail-closed (abstener-y-listar) cubre los casos que la heurística no desambigua.
> Todo en UTC (tz por práctica diferido).

## Agente — `app/agents/cancel_agent.py`

```python
class ProposedCancellation(BaseModel):              # structured output del extractor (args tipados)
    client_name: str
    when: str | None = None                         # pista ISO 8601 opcional ("el martes"→fecha; null si no se dijo)

async def propose_cancellation(
    question: str, practice_id: str, *, now: datetime, gen_llm=None,
) -> ProposalResult                                 # misma firma que propose_appointment/propose_interaction
```

### Extracción (`gemma4:12b`, `with_structured_output(ProposedCancellation)`)
- Args tipados → `with_structured_output` es confiable en el 12B (lo prueban `ProposedAppointment` /
  `ProposedInteraction`; el gotcha del `None` intermitente es del `e4b`/clasificador, ver §Clasificador).
- Prompt (a mano, español): rol = asistente de agenda; "extraé el cliente cuyo turno se va a **cancelar** y,
  **si se menciona**, la fecha/hora del turno (resolvé 'mañana'/'el martes' a ISO 8601 absoluto contra
  {now} UTC). Si no se menciona fecha, dejá `when` en null." Fail-closed: excepción → `None` → abstención
  genérica *"No pude identificar qué turno cancelar. ¿Me decís el cliente y, si podés, la fecha?"*.

### Resolución determinística (sin LLM, scoped por `practice_id`)
1. `resolve_single_client(practice_id, extracted.client_name, limit=settings.appt_name_match_limit)` →
   reusa los mensajes de abstención del Slice 5 (vacío / no encontrado / ambiguo).
2. Parsear `when` (si no es `None`): `datetime.fromisoformat`; tz-naive → UTC. **`ValueError` → `when=None`**
   (degrada a "sin pista"; la fecha es opcional, no se aborta — el resolver listará si hay ambigüedad).
3. `resolve_single_appointment(practice_id, client, when, now=now, limit=settings.appt_name_match_limit)` →
   si `appointment is None`, abstención con su mensaje/reason.
4. `proposed_action` (viaja al `interrupt`, se checkpointea):
   ```python
   {
     "kind": "cancel_appointment",
     "summary": "Cancelar el turno de Ana López con Dra. Gómez el 01/07 10:00 (UTC)",   # texto de la card
     "params": {
       "appointment_id": "…",                       # ÚNICO campo que usa el writer
       "client_name": "Ana López",                  # display (card/recibo)
       "practitioner_name": "Dra. Gómez",           # display
       "start_at": "2026-07-01T10:00:00+00:00",     # display
     },
   }
   ```
   El `summary` de la card se arma determinísticamente del turno resuelto + cliente (no re-consulta la DB).

## Tool parametrizada — `app/db.py`

```python
async def find_cancellable_appointments(
    practice_id: str, client_id: str, *, now: datetime, limit: int,
) -> list[dict[str, Any]]:
```
```sql
SELECT a.id::text, a.start_at, a.end_at, a.status,
       a.practitioner_id::text, p.full_name AS practitioner_full_name
FROM appointments a
JOIN practitioners p ON a.practitioner_id = p.id
WHERE a.practice_id = $1 AND a.client_id = $2
  AND a.start_at >= $3 AND a.status IN ('programado','confirmado')
ORDER BY a.start_at
LIMIT $4;
```

```python
async def cancel_appointment(practice_id: str, appointment_id: str) -> dict[str, Any] | None:
```
```sql
UPDATE appointments SET status = 'cancelado'
WHERE id = $1 AND practice_id = $2 AND status IN ('programado','confirmado')
RETURNING id::text, status, start_at;
```
- Devuelve la fila actualizada, o **`None`** si no matcheó (turno de otra práctica / inexistente, o ya no
  cancelable por estado → idempotencia y TOCTOU). El guard `practice_id` es el cinturón multi-tenant (mismo
  espíritu que el `EXISTS` de `create_appointment`/`log_interaction`); el guard de `status` evita doble
  cancelación y cancelar lo ya atendido. Parámetros `$n` (asyncpg), **sin interpolar texto** (lo opuesto al
  SQL-libre prohibido por §4).
- No re-chequea `start_at >= now` (ver Decisión #4). No necesita `now`.

## Registry — `app/agents/write_tools.py`

```python
# ---- cancel_appointment ----
async def _write_cancel(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = await db.cancel_appointment(practice_id, params["appointment_id"])
    return {"cancelled": True, **row} if row is not None else {"cancelled": False}

def format_cancel_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("cancelled"):
        return "⚠️ No pude cancelar el turno: ya no estaba disponible (puede haberse cancelado o atendido)."
    start = datetime.fromisoformat(params["start_at"])
    return (
        f"✅ Turno cancelado: {params['client_name']} con {params['practitioner_name']} "
        f"el {start.strftime('%d/%m %H:%M')} (UTC)."
    )

REGISTRY["cancel_appointment"] = WriteTool(
    kind="cancel_appointment",
    propose=propose_cancellation,             # de cancel_agent.py
    write=_write_cancel,                       # adapter: params → db.cancel_appointment; envuelve el None
    format_receipt=format_cancel_receipt,
    cancel_message="Listo, dejé el turno como estaba.",
)
```

- `_write_cancel` es el **adapter** (mismo patrón que `_write_appointment`/`_write_interaction`): mapea
  `params` al writer y **descarta** las claves display (`client_name`/`practitioner_name`/`start_at` no son
  argumentos del `UPDATE`). Envuelve el `None` del writer en `{"cancelled": False}` para que
  `format_cancel_receipt` emita un recibo cordial en vez de explotar (el writer **no** levanta excepción en
  el camino "ya no cancelable": es un resultado esperado, no un error).
- **`WRITE_KINDS`** pasa a `("create_appointment", "log_interaction", "cancel_appointment", "unsupported")`
  (sin solapamiento de substrings → el match exacto/substring de `classify_write_action` sigue siendo fiable).
- **`CLASSIFY_PROMPT`** (extendido; saca cancelar de `unsupported`, contrasta create=nuevo vs cancel=existente):
  ```
  - create_appointment: agendar/crear un turno NUEVO. Ej: "agendá un turno para Ana mañana 10",
    "dale una cita a Juan el martes", "reservá un turno con la Dra. Gómez".
  - log_interaction: registrar/anotar una interacción YA OCURRIDA con un cliente (sesión, llamada, email,
    nota, mensaje). Ej: "registrá que llamé a Ana", "anotá una nota sobre Juan".
  - cancel_appointment: cancelar/anular un turno YA EXISTENTE. Ej: "cancelá el turno de Juan",
    "anulá la cita de Ana del martes", "cancelá el turno de las 10 de Pedro".
  - unsupported: cualquier OTRA acción de escritura que NO sea esas tres (REPROGRAMAR/EDITAR un turno,
    dar de baja un cliente, facturar). Ej: "reprogramá el turno de Juan", "cambiá la hora de la cita".
  Respondé solo con la opción.
  ```
- **Sin cambios** en el mecanismo de `classify_write_action` (ya es `ainvoke` + text-parse + retry +
  fallback `unsupported`, por el gotcha del `None` intermitente del `e4b`, CLAUDE.md §4 / addendum Slice 5).

## Nodos del grafo — `app/graph/nodes.py`

Los nodos ya son genéricos por `kind` (Slice 5) → **no cambian su lógica**. Dos toques mínimos:

1. **Copy de capacidades** en `propose_action_node` (rama `unsupported` / `kind not in REGISTRY`): el mensaje
   *"Por ahora puedo agendar turnos o registrar interacciones. ¿Cuál de las dos necesitás?"* pasa a incluir
   cancelar: *"Por ahora puedo agendar turnos, registrar interacciones o cancelar turnos. ¿Qué necesitás?"*.
   (Honestidad de capacidades; es copy, no lógica.)
2. **Cleanup opcional aprobado** (fast-follow del review de Slice 5): en `confirm_action_node`,
   `action = state["proposed_action"] or {}` — el `or {}` es código muerto (`route_after_propose` ya manda a
   `END` si `proposed_action is None`, así que en `confirm_action` nunca es `None`). Se reemplaza por
   `action = state["proposed_action"]; assert action is not None  # route_after_propose garantiza no-None`
   (documenta el invariante y satisface a mypy). 1 línea, bajo riesgo, en la zona que toca el slice.

`edges.py`, `build.py`, `state.py`, transporte (`main.py`): **sin cambios**.

## Frontend

**Sin cambios funcionales.** `ConfirmCard.tsx` rinde `action.summary` + los tokens del recibo, agnóstica al
`kind` (verificado: ya se reusó para `log_interaction` en Slice 5). El evento SSE `confirm` y `/chat/resume`
no dependen de la tool.

- **Arruga de UX anotada (no bloqueante)**: al proponer una **cancelación**, la tarjeta muestra "Cancelar el
  turno de Ana…" con botones **[Confirmar] [Cancelar]** → *Confirmar* = sí, cancelá el turno; *Cancelar* = no,
  dejalo. El `summary` explícito ("Cancelar el turno de … ¿Confirmás?") lo hace legible. Afinar el copy de los
  botones (p. ej. "Sí, cancelar" / "No") es parte del canvas rico de Fase 1, diferido.
- **Opcional (contrato)**: un caso en `ConfirmCard.test.tsx` con un `action` de `kind:"cancel_appointment"`
  para fijar que la card sigue siendo genérica. Es test, no producto.

## Config (`config.py`)

**Sin vars nuevas.** El resolver de cliente y el de turno reusan `settings.appt_name_match_limit` (ya existe;
acota el `LIMIT` del finder de turnos y del ILIKE de clientes). Modelos: clasificador `gemma4:e4b`;
extracción `gemma4:12b`; **sin LLM** en resolver/escritura/recibo.

## Multi-tenant (CLAUDE.md §0.5)

`practice_id` viaja en `AgentState` (de `settings`, single-tenant en dev). El **finder** filtra por
`practice_id` + `client_id`; el **`UPDATE`** re-verifica `practice_id` en el `WHERE`. No hay forma de cancelar
un turno de otra práctica (ni resolverlo: el finder no lo trae). Pre-RLS el aislamiento es **app-level**
(resolver scopeado + guard en el `UPDATE`); RLS en Postgres = Fase 4 (consistente con Slices 3/4/5).

## Seguridad / guardrails

- **HITL inquebrantable**: sin confirmación explícita, **no hay mutación**. Abstención, `unsupported` y
  "cancelar" (en la tarjeta) no escriben. El `interrupt`/`resume` no recomputa la propuesta (2 nodos, Slice 4).
- **Tool parametrizada, no SQL libre** (§4): `cancel_appointment` usa `$n`; el LLM solo produce
  `client_name` + `when` (texto/pista), nunca el `appointment_id` ni el SQL.
- **Idempotencia / doble-submit**: el guard de `status` en el `UPDATE` hace que una segunda confirmación (o un
  turno ya cancelado por otra vía) matchee 0 filas → recibo "⚠️ ya no estaba disponible", sin efecto. La
  idempotencia del `resume` (un solo interrupt pendiente lo consume) se hereda del Slice 4.
- **PII**: este slice **no captura texto libre** (a diferencia de `log_interaction.content`) → no agrega
  superficie de PII. Tarjeta/recibo muestran nombre del cliente/profesional al usuario autorizado; los logs
  registran `kind` + ids + decisión, no nombres en crudo. Redacción en entrada/salida = slice de Guardrails.
- **Inyección**: la frase del usuario nunca se concatena a SQL; la pista de fecha se parsea con
  `datetime.fromisoformat` (falla → `None`), no se ejecuta. Detección de inyección en entrada = Guardrails.

## Testing (DoD CLAUDE.md §6)

Patrón establecido: inyección de `gen_llm=` y `monkeypatch` de funciones de módulo
(`tests/test_action_agent.py`, `test_interaction_agent.py`, `test_nodes.py`, `test_hitl_cycle.py`,
`test_write_tools.py`).

- **No-llm** (sin Ollama):
  - `test_db.py` (extender, integración DB real local):
    - `find_cancellable_appointments`: devuelve solo futuros con `status ∈ {programado, confirmado}`,
      ordenados por `start_at`, con `practitioner_full_name`; **excluye** pasados, `atendido`/`ausente`/
      `cancelado`, y turnos de **otra** `practice_id` o de **otro** `client_id`.
    - `cancel_appointment`: cancela (devuelve fila con `status='cancelado'`); **rechaza** (→ `None`) turno de
      otra `practice_id`; **rechaza** (→ `None`) turno ya `cancelado`/`atendido` (guard de estado);
      doble-cancel → la 2ª llamada devuelve `None`.
  - `test_resolvers.py` (extender): `resolve_single_appointment` con `db.find_cancellable_appointments`
    monkeypatcheado → 0 turnos → `appointment_none`; 1 → `ok` con el turno; varios sin `when` →
    `appointment_ambiguous` (mensaje lista los candidatos); varios con `when` que filtra a 1 → `ok`; `when`
    de un día sin turnos → `appointment_not_found` (lista los próximos reales); `when` con hora que desempata
    entre dos del mismo día → `ok`.
  - `test_cancel_agent.py` (nuevo): `propose_cancellation` con `gen_llm` fake (devuelve `ProposedCancellation`)
    y `db.*` / resolvers monkeypatcheados → cliente 1 + turno 1 → `proposed_action` con `kind`,
    `params["appointment_id"]` y `summary` legible; extractor falla (`None`) → abstención genérica; cliente
    0/>1 → abstención (reusa mensajes de `resolve_single_client`); turno 0/>1 → abstención del resolver de
    turno; `when` no parseable → no aborta (se trata como sin pista; el resolver decide).
  - `test_write_tools.py` (extender): `REGISTRY["cancel_appointment"]` existe y es coherente; `WRITE_KINDS`
    lo incluye; `classify_write_action` con `llm` fake rutea "cancelá el turno de X" → `cancel_appointment`,
    "reprogramá el turno de X" → `unsupported`, y las frases de create/log siguen a su kind (no-regresión del
    clasificador); `_write_cancel` con `db.cancel_appointment` monkeypatcheado → fila → `{"cancelled": True,…}`,
    `None` → `{"cancelled": False}`; `format_cancel_receipt` ramas ok ("✅ Turno cancelado…") y no-ok ("⚠️…").
  - `test_nodes.py` (extender): el mensaje de capacidades de `propose_action_node` (rama `unsupported`)
    menciona cancelar; `confirm_action_node` parametrizado con `kind="cancel_appointment"`: confirm → llama
    `tool.write` una vez con `params` y el recibo trae "✅"; cancel → `tool.write` **no** se llama y usa
    `cancel_message` correcto. (El dispatch genérico ya está cubierto; se agrega el caso del nuevo kind.)
  - `test_hitl_cycle.py` (extender/parametrizar): el ciclo `interrupt`→`resume` con `kind="cancel_appointment"`
    (`MemorySaver`, `propose_*` monkeypatcheado a un `ProposalResult` ya resuelto) → `resume="confirm"` →
    `write` espiado se llamó una vez; `resume="cancel"` → no se llamó (no recomputa al reanudar).
  - **No-regresión**: los tests existentes de `create_appointment` / `log_interaction` (agentes, writers,
    ciclo HITL, registry) siguen verdes sin cambiar sus asertos.
- **`-m llm`** (`test_cancel_e2e_llm.py`, nuevo; Ollama + Postgres reales, `seed_demo.py` corrido):
  - tomar un cliente del seed que tenga un turno futuro `programado`; *"cancelá el turno de \<nombre\>"* →
    ciclo `ainvoke` → interrupt con `proposed_action` (`kind=="cancel_appointment"`, `appointment_id` poblado);
    `resume="confirm"` → la fila pasa a `status='cancelado'` en `appointments` (verificado por `id`);
    `resume="cancel"` → el turno queda intacto. Aserto de **clasificación**: la frase de cancelar clasifica
    `cancel_appointment` (no `create_appointment`, pese a compartir "turno"). Limpieza: el test re-`UPDATE`a el
    estado o usa un turno creado ad-hoc para no dejar el seed mutado entre corridas.
- **Frontend** (`vitest`): opcional, caso de contrato `ConfirmCard` con `action` de cancelación. Verde:
  vitest + lint + build (sin cambios de producto).
- **Gates**: `ruff check . && ruff format .`; `mypy --config-file backend/pyproject.toml` (siempre con la
  config: sin ella, falso-positivo `asyncpg [import-untyped]`); `pytest -q` (no-llm) verde. **Smoke §2**:
  *"cancelá el turno de \<cliente\> del \<día\>"* → **abre tarjeta** → Confirmar → ✅ + la fila queda
  `cancelado` en la DB; Cancelar → intacto; pedido ambiguo → abstiene listando; y *"agendá un turno…"* /
  *"registrá que llamé a…"* siguen abriendo tarjeta y escribiendo (no-regresión).

## Dependencias

Ninguna nueva. `langgraph` ya provee `interrupt`/`Command` (checkpointer Postgres cableado en el `lifespan`);
`with_structured_output` ya se usa; el registry y la `ConfirmCard` ya existen. Sin red saliente fuera de
Ollama/Postgres/Qdrant locales (DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff`, `mypy --config-file backend/pyproject.toml`, `pytest -q` (no-llm) verdes; `-m llm` verde con
   Ollama + ambos modelos + Postgres + `seed_demo.py` corrido.
2. Tocamos el grafo (vía registry) y agregamos una tool de escritura: smoke §2 pasa, **las escrituras piden
   confirmación de verdad**, y las dos tools previas **no regresionan**. El ciclo `interrupt`→`resume` tiene
   test no-llm para el nuevo `kind`.
3. No se tocó retrieval/SQL/síntesis ni el **prompt del router** → la suite offline de eval no aplica. El
   **clasificador de write-actions** cambió (nuevo kind + ejemplos): si el e2e mostrara un fallo de
   clasificación, se agrega un caso golden — anotado.
4. Prompts (clasificador extendido + extractor de cancelación) a mano ahora; recompilar con DSPy = Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales.
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **Clasificación errónea create vs. cancel** (comparten "turno/cita"): riesgo medio — se mitiga con
  contraste explícito por verbo (agendá/reservá vs. cancelá/anulá) y "nuevo" vs. "existente" en el prompt;
  el text-parse + retry + fallback `unsupported` ya absorbe el `None` intermitente del `e4b`; aserto de
  clasificación en el e2e; caso golden si aparece un fallo.
- **Resolución del turno objetivo ambigua** (cliente con varios turnos): por diseño se **abstiene listando**,
  fail-closed. La pista de fecha opcional cubre el caso común ("del martes") en un solo tiro; el resto espera
  al slice de memoria (slot-filling). No se adivina nunca qué cancelar.
- **TOCTOU / doble-cancel** (turno cambia entre propuesta y confirmación): el guard de `status` en el
  `UPDATE` → 0 filas → recibo cordial "⚠️ ya no estaba disponible", sin efecto. Cubierto en `test_db`.
- **Heurística de `when`** (día/hora): documentada como limitación; el camino fail-closed cubre lo que no
  desambigua. Parseo robusto (`ValueError` → degradar a sin-pista, no abortar).
- **`with_structured_output` para `ProposedCancellation`**: bajo riesgo (args tipados chicos funcionan en el
  12B). Fallback: `format=` JSON Schema de Ollama (misma decodificación restringida, §4) — anotado, no esperado.
- **Aislamiento tenant pre-RLS**: app-level (finder scopeado + guard `practice_id` en el `UPDATE`); RLS en
  Fase 4 es el cierre real.
- **Arruga de UX del doble "Cancelar"** (botón "Cancelar" sobre una tarjeta de "Cancelar turno"): mitigada por
  el `summary` explícito; afinar copy de botones = canvas rico de Fase 1, diferido.
