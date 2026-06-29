# Praxia · Fase 1 · Slice 7 — Write-tools `reschedule_appointment` + `update_client` (4ª y 5ª tools)

> Diseño aprobado el 2026-06-29. Spec de un único slice implementable (dos tools hermanas sobre el mismo registry).
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md`.
> Slices previos: grafo + router (`2026-06-25-grafo-router-design.md`),
> subgrafo CRAG (`2026-06-26-crag-design.md`),
> Data Agent NL2SQL read-only (`2026-06-26-nl2sql-data-agent-design.md`),
> write-tool `create_appointment` con HITL (`2026-06-27-write-appointment-hitl-design.md`),
> `log_interaction` + registry de write-tools (`2026-06-28-log-interaction-design.md`),
> `cancel_appointment` (1ª mutación) + `resolve_single_appointment` (`2026-06-29-cancel-appointment-design.md`).

## Objetivo

Agregar las **tools de escritura 4ª y 5ª** de Praxia sobre el registry del Slice 5, sin tocar router,
transporte ni front:

- **`reschedule_appointment`** — reprogramar (mover fecha/hora) un turno existente. Es la **2ª mutación**
  (la 1ª fue `cancel_appointment`). **Reusa la pieza cara del Slice 6** —
  `resolve_single_appointment` (`agents/resolvers.py`)— para resolver *cuál* turno mover; lo nuevo respecto a
  cancelar es que extrae **dos** referencias temporales (qué turno + nueva fecha/hora) y computa el nuevo
  `end_at` preservando la duración.
- **`update_client`** — editar **campos estructurados** del cliente (teléfono, email, estado, fecha de
  nacimiento). Reusa `resolve_single_client` (Slice 5). Es un **UPDATE parcial** (`COALESCE`): cambia solo lo
  que el usuario nombró. **Excluye `notes` (texto libre) a propósito**: texto libre = posible PII sin
  redacción de Presidio (que aún no existe), así que `notes` queda diferido al slice de Guardrails (CLAUDE.md
  §0/§5). Los campos estructurados (teléfono, email, etc.) **son** PII, pero son *el payload mismo* de la
  operación —un CRM existe para guardarlos— y entran por una tool parametrizada, no por texto libre.

Entregables observables:

- *"reprogramá el turno de \<cliente\> del martes para el jueves a las 15"* → el grafo **clasifica**
  `reschedule_appointment`, **resuelve** el cliente y el **turno objetivo** (turnos futuros cancelables,
  filtrando por la pista de fecha actual), valida la nueva fecha y **se pausa** mostrando la **misma
  `ConfirmCard`**: *"Reprogramar el turno de Ana López con la Dra. Gómez: 01/07 10:00 → 03/07 15:00 (UTC).
  ¿Confirmás?"*. **Confirmar** → `UPDATE appointments SET start_at, end_at` (guard tenant + estado) → recibo
  *"✅ Turno reprogramado: Ana López con la Dra. Gómez → 03/07 15:00 (UTC)."*.
- *"cambiá el teléfono de \<cliente\> a 11-2233-4455"* / *"dá de baja a \<cliente\>"* → clasifica
  `update_client`, resuelve el cliente, arma el **antes→después** y se pausa: *"Actualizar Ana López:
  teléfono 11-1111-1111 → 11-2233-4455. ¿Confirmás?"*. **Confirmar** → `UPDATE clients SET … (COALESCE)` →
  recibo *"✅ Datos actualizados de Ana López: teléfono → 11-2233-4455."*.
- **Cancelar** (en cualquiera de las dos tarjetas) → no se escribe nada → mensaje cordial.
- Las **tres tools previas** (`create_appointment`, `log_interaction`, `cancel_appointment`) **siguen
  funcionando igual** (no-regresión del registry y del clasificador).
- Pedido ambiguo (varios turnos futuros y la frase no desempata; o varios clientes con ese nombre) →
  **abstención fail-closed** listando candidatos, sin tarjeta, sin escribir.
- Datos no resolubles con seguridad (cliente/turno inexistente o ambiguo; falta el dato a cambiar; nueva
  fecha en el pasado) → **abstención fail-closed**, sin tarjeta, sin escribir.
- Acción de escritura que **aún no es tool** (*"agregá una nota sobre Juan"* —texto libre—, *"facturá la
  sesión"*) → `unsupported` → abstención cordial.

Gate que cierra el slice (CLAUDE.md §2/§6): el smoke reprograma un turno y actualiza un cliente **solo tras
confirmar** (las filas cambian en la DB), las tres tools previas siguen verdes, y **ninguna escritura ocurre
sin confirmación**.

## No-objetivos (diferidos, cada uno trabajo propio posterior)

- **`update_client.notes` (texto libre)**: el gap de PII real. Su lugar es **después de Guardrails** (Presidio
  redacta PII en la entrada). El clasificador manda "agregá/editá una nota de cliente" a `unsupported` hasta
  entonces. Las notas de turno (`appointments.reason`) y de interacción (`interactions.content`) siguen igual.
- **Chequeo de solapamiento / doble-booking al reprogramar**: el MVP **no** verifica que el profesional esté
  libre en la nueva franja (igual que `create_appointment` hoy no lo verifica). Mover a un horario ocupado es
  posible; detectar conflictos = follow-up (compartido con `create_appointment`). Anotado como fast-follow.
- **Cambiar de profesional al reprogramar**: este slice solo **mueve el tiempo** (mismo profesional, misma
  duración salvo lo que cambie el nuevo horario). Reasignar profesional = otra operación, fuera de alcance.
- **`update_client` de `full_name`, `tags`, `dob` masivo, o multi-campo con borrado**: se cubren
  teléfono/email/estado/fecha-nacimiento, y `COALESCE` **solo setea** (no borra un campo a `NULL`). Renombrar
  un cliente (es la clave de matcheo de los resolvers) o limpiar campos = decisiones propias, diferidas.
- **Slot-filling multi-turno del "¿cuál turno?"**: ambas propuestas son **one-shot**. Si quedan varios
  candidatos, abstiene listándolos; no pregunta de a uno ni recuerda la conversación. El "¿cuál?" en un turno
  siguiente requiere `thread_id` estable + memoria de corto plazo → su propio slice. Por eso `reschedule`
  extrae una **pista de fecha actual opcional** (igual que `cancel`).
- **Motivo de la reprogramación / notificación al cliente**: el MVP solo cambia `start_at`/`end_at`. Guardar
  motivo o notificar = posterior.
- **`agent_runs` / audit log formal + `consents`**: siguen sin existir; la fila mutada es el registro por
  ahora. Audit formal = Fase 4 (consistente con Slices 4/5/6).
- **Servidor MCP `mcp_servers/mcp_postgres.py`**: como las tools previas, ambas se construyen **in-process**
  (`db.*` + `agents/*_agent.py`); el contrato §4 se cumple en espíritu (tool parametrizada + HITL, sin SQL del
  LLM). El wrapper MCP llega cuando lo pida el dev loop (§8) o prod.
- **Timezone por práctica**: todo en UTC, etiquetado `(UTC)` en tarjeta/recibo (igual que Slices 4/5/6).
- **Compilar prompts con DSPy**: clasificador y extractores a mano ahora; recompilar = Fase 2.

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). Inferencia 100% local por Ollama: el clasificador
(`gemma4:e4b`) elige la tool; `gemma4:12b` extrae los argumentos estructurados; **no hay LLM en los resolvers
ni en las escrituras** (determinísticos). El grafo es la fuente de control: las tools se enchufan detrás del
router (intent `action`) vía el registry; ningún camino esquiva router/guardrails. **Lectura y escritura
separadas por diseño**: las mutaciones nunca son SQL libre — son tools parametrizadas (`UPDATE` con `$n`) y
**siempre** detrás del `interrupt` de confirmación. Aislamiento por `practice_id` en la resolución y en cada
`UPDATE` (CLAUDE.md §0.5). **Fail-closed**: ante cualquier ambigüedad (cliente, turno, o qué dato cambiar), se
abstiene y lista; nunca adivina qué mutar. **PII estructurada vs. texto libre**: este slice escribe PII
estructurada (teléfono/email) que **es** el payload de la operación, vía tool parametrizada; el texto libre
(`notes`) —donde la PII es incidental y no redactada— queda fuera hasta Guardrails.

## Arquitectura

### Decisión de límites #1 — Reusar el registry tal cual (dos descriptores + clasificador)

El Slice 5 dejó el camino de escritura genérico: `propose_action_node` clasifica el `kind` y delega en
`REGISTRY[kind].propose`; `confirm_action_node` hace `interrupt(action)` y delega en
`REGISTRY[kind].write/.format_receipt`. Agregar dos tools = **registrar dos `WriteTool`** + **extender el
clasificador** (`CLASSIFY_PROMPT` + `WRITE_KINDS`). Router, nodos, edges, transporte SSE y `ConfirmCard`
**no cambian** (salvo una línea de copy de capacidades). Es el dividendo del registry, ahora por partida doble.

### Decisión de límites #2 — `reschedule` reusa el resolver de turno del Slice 6 (no hay pieza cara nueva)

Cancelar exigió crear `resolve_single_appointment` (resolver fail-closed de la fila a mutar). Reprogramar usa
ese **mismo** resolver sin tocarlo: el turno objetivo se resuelve idéntico (cliente → turnos cancelables →
filtrar por pista de fecha → 1 / 0 / >1). Lo único nuevo de `reschedule` respecto a `cancel` es **el destino**
(la nueva fecha/hora) y **preservar la duración**. Esto hace al slice barato: el 80% del riesgo (resolver
ambiguo, scoping, fail-closed) ya está construido y testeado.

### Decisión de límites #3 — extracción de doble referencia temporal en `reschedule`

El usuario da **dos** tiempos: cuál turno ("del martes") y a dónde moverlo ("al jueves 15"). El extractor saca
`client_name` + `current_when` (pista del turno actual, **opcional**) + `new_start_at` (destino,
**obligatorio**):

1. `current_when` desambigua *cuál* turno (vía `resolve_single_appointment(when=current_when)`, igual que
   `cancel`). Si falta y el cliente tiene >1 turno futuro → **abstiene listando** (fail-closed).
2. `new_start_at` es el destino; si no se puede parsear → abstención (`datetime_parse_failed`).

Alternativa descartada: resolver interactivo ("¿cuál de estos movés?") → necesita memoria de corto plazo, que
no existe. Dentro de un turno, la extracción one-shot + abstención fail-closed es la única opción consistente
con el resto del código.

### Decisión de límites #4 — `reschedule`: preservar duración · rechazar pasado · sin chequeo de solapamiento

- **Duración preservada**: `new_end_at = new_start_at + (old.end_at − old.start_at)`. Mover un turno no le
  cambia el largo; computar el `end_at` evita pedirle al usuario algo que ya está implícito.
- **Rechazar pasado en la propuesta**: `new_start_at >= now` se valida al **proponer** (no tiene sentido mover
  un turno al pasado). Si es pasado → abstención cordial pidiendo una fecha futura.
- **El writer NO re-chequea `start_at >= now`** (simétrico a `cancel`): no queremos fallar por "confirmaste 2
  minutos tarde". El guard del writer es **estado** (`status ∈ {programado, confirmado}`) — eso es lo que puede
  cambiar de forma significativa entre propuesta y confirmación (TOCTOU). El destino ya se validó al proponer.
- **Sin chequeo de doble-booking** (igual que `create_appointment`): fast-follow, no MVP.

### Decisión de límites #5 — `update_client` estructurado, `COALESCE`, sin `notes` (§0)

El extractor saca solo campos **enumerados/estructurados**: `phone`, `email`, `status`
(`Literal["activo","inactivo","baja"]`), `dob` (fecha ISO). El `UPDATE` usa `COALESCE($n, col)` → un comando
puede cambiar **uno o varios** campos; los no provistos quedan intactos (no se borran). **`notes` no es campo
del extractor ni del `UPDATE`** → no entra texto libre. "dar de baja" mapea a `status='baja'`. La validación
del enum la fuerza el `CHECK` del schema (defensa en profundidad sobre el `Literal` del extractor).

### Decisión de límites #6 — `update_client` muestra antes→después (`db.get_client`)

`find_clients_by_name` (Slice 5) devuelve solo `{id, full_name}` → no alcanza para mostrar "teléfono actual →
nuevo". Se agrega `db.get_client(practice_id, client_id)` (fetch del row del cliente, scopeado). La tarjeta
muestra **antes→después** por cada campo a cambiar. Razón HITL: en una edición que **sobrescribe**, ver el
valor actual deja atrapar "cliente equivocado" o "campo equivocado" antes de confirmar; la tarjeta no debe ser
la única red. (Alternativa descartada: mostrar solo el valor nuevo → más barato pero menos seguro.)

### Decisión de límites #7 — un archivo por tool

```
backend/app/
├── db.py                          # +reschedule_appointment(...)  (UPDATE start_at/end_at, guard practice_id+status)
│                                  # +get_client(...)              (SELECT scopeado, para el antes→después)
│                                  # +update_client(...)           (UPDATE parcial COALESCE, guard practice_id)
├── agents/
│   ├── resolvers.py               # SIN CAMBIOS: reschedule reusa resolve_single_appointment tal cual
│   ├── reschedule_agent.py        # NUEVO: ProposedReschedule + propose_reschedule
│   ├── update_client_agent.py     # NUEVO: ProposedClientUpdate + propose_update_client
│   └── write_tools.py             # +adapters + receipts + REGISTRY["reschedule_appointment"/"update_client"]
│                                  #  + WRITE_KINDS y CLASSIFY_PROMPT extendidos
└── graph/
    └── nodes.py                   # SOLO copy: mensaje de capacidades de propose_action_node
```

- `action_agent.py`, `cancel_agent.py`, `interaction_agent.py` y `resolvers.py` quedan **intactos**.
- `ProposalResult` se sigue importando de `action_agent.py` (como hacen `interaction_agent`/`cancel_agent`); no
  se mueve a un módulo compartido en este slice (consistencia > churn).
- Regla CLAUDE.md §3: la lógica nueva (extractores, adapters) vive en `agents/`; los nodos solo orquestan y ya
  son genéricos por `kind` → no se tocan (salvo copy).

## Flujo de datos

Ambas tools entran por el mismo camino genérico (router → `propose_action` → `confirm_action`). Difieren solo
en el `propose` del registry.

```
 "reprogramá el turno de Ana del martes al jueves 15"   |   "cambiá el teléfono de Ana a 11-2233-4455"
        │  router (intent = action)  — prompt del router SIN cambios
        ▼
 propose_action
   1) classify_write_action (e4b, text-parse) → kind ∈ {create_appointment, log_interaction,
        cancel_appointment, reschedule_appointment, update_client, unsupported}
   2a) unsupported → abstención cordial ─────────────────────────────────────► END
   2b) REGISTRY[kind].propose(question, practice_id, now=…)

   ── reschedule_appointment ──────────────────┐   ── update_client ─────────────────────────┐
   12b → ProposedReschedule                     │   12b → ProposedClientUpdate                 │
     {client_name, current_when?, new_start_at} │     {client_name, phone?, email?, status?,   │
   resolve_single_client (scoped)               │      dob?}                                   │
   parse new_start_at (oblig.); pasado→abstiene │   resolve_single_client (scoped)             │
   parse current_when (opc.; ilegible→None)     │   recolectar campos no-None; dob ilegible→   │
   resolve_single_appointment (Slice 6, scoped) │     descarta; sin campos→abstiene            │
     0/>1 → abstención listando                 │   db.get_client (antes→después)              │
   new_end = new_start + (old.end − old.start)  │                                              │
   ────────────────────────────────────────────┘──────────────────────────────────────────────┘
        ▼
   ¿resolvió? no → abstención cordial (sin tarjeta) ─► END
   sí ▼  proposed_action = {kind, summary(card), params{… ids resueltos + display}}
 confirm_action
   decision = interrupt(proposed_action)  ⏸ → /chat emite SSE `confirm`; pausa
   POST /chat/resume → Command(resume="confirm"|"cancel")
   confirm → REGISTRY[kind].write(practice_id, params):
      reschedule → db.reschedule_appointment(…)  UPDATE start_at,end_at (guard practice_id+status)
      update_client → db.update_client(…)        UPDATE … COALESCE (guard practice_id)
      → fila → "✅ …"   |   0 filas/None → "⚠️ …"
   cancel  → cancel_message de la tool
        ▼
       END
```

Reglas del flujo (heredadas de Slices 4/5/6, ya genéricas):
- **One-shot**: la propuesta no pregunta de a uno; falta de datos o ambigüedad → abstención fail-closed.
- **Cualquier excepción** en clasificación/extracción/resolución → abstención (no abre tarjeta, no escribe).
- **El LLM nunca toca UUIDs**: da nombre + pistas; los `appointment_id`/`client_id` salen de la DB scopeada por
  `practice_id`. El writer recibe solo ids resueltos + valores estructurados.
- **Recibos y cancelación de tarjeta son texto determinístico** → `/chat/resume` no necesita Ollama.

## Agente — `app/agents/reschedule_agent.py`

```python
class ProposedReschedule(BaseModel):            # structured output del extractor (args tipados, 12B)
    client_name: str
    current_when: str | None = None             # pista ISO opcional del turno ACTUAL (desambigua cuál)
    new_start_at: str                           # nueva fecha/hora del turno (OBLIGATORIA, ISO 8601)

async def propose_reschedule(
    question: str, practice_id: str, *, now: datetime, gen_llm=None,
) -> ProposalResult                             # misma firma que propose_cancellation
```

### Extracción (`gemma4:12b`, `with_structured_output(ProposedReschedule)`)
- Args tipados → `with_structured_output` confiable en el 12B (lo prueban `ProposedAppointment`/
  `ProposedCancellation`; el `None` intermitente es del `e4b`/clasificador).
- Prompt (a mano, español): rol = asistente de agenda; "extraé el cliente, la pista del turno **actual** si se
  menciona (`current_when`), y la **nueva** fecha/hora a la que se mueve (`new_start_at`). Resolvé
  'mañana'/'el jueves' a ISO 8601 absoluto contra {now} UTC. En 'del X al Y', `current_when`=X y
  `new_start_at`=Y. Si solo se da una fecha, es `new_start_at` (el destino) y `current_when`=null." Excepción →
  `None` → abstención genérica *"No pude entender la reprogramación. Decime el cliente y la nueva fecha/hora."*.

### Resolución determinística (sin LLM, scoped por `practice_id`)
1. `resolve_single_client(practice_id, client_name, limit=settings.appt_name_match_limit)` (reusa mensajes de
   abstención del Slice 5).
2. Parsear `new_start_at` (`datetime.fromisoformat`, tz-naive→UTC). **`ValueError` → abstención**
   (`datetime_parse_failed`, *"No entendí la nueva fecha/hora del turno."*). Es obligatoria, no se degrada.
3. Si `new_start_at < now` → abstención (`new_time_past`, *"Esa fecha ya pasó; decime una futura para mover el
   turno."*).
4. Parsear `current_when` (si no es `None`): `ValueError → None` (degrada a "sin pista"; opcional).
5. `resolve_single_appointment(practice_id, client, current_when, now=now, limit=settings.appt_name_match_limit)`
   → si `appointment is None`, abstención con su mensaje/reason (reusa el resolver del Slice 6 sin cambios).
6. `new_end_at = new_start_at + (appt["end_at"] - appt["start_at"])` (preserva la duración).
7. `proposed_action`:
   ```python
   {
     "kind": "reschedule_appointment",
     "summary": "Reprogramar el turno de Ana López con Dra. Gómez: 01/07 10:00 → 03/07 15:00 (UTC)",
     "params": {
       "appointment_id": "…",                       # writer
       "new_start_at": "2026-07-03T15:00:00+00:00",  # writer (ISO; el adapter parsea)
       "new_end_at":   "2026-07-03T15:30:00+00:00",  # writer
       "client_name": "Ana López",                   # display
       "practitioner_name": "Dra. Gómez",            # display
       "old_start_at": "2026-07-01T10:00:00+00:00",  # display (para el summary/recibo)
     },
   }
   ```

## Agente — `app/agents/update_client_agent.py`

```python
class ProposedClientUpdate(BaseModel):          # structured output del extractor
    client_name: str
    phone: str | None = None
    email: str | None = None
    status: Literal["activo", "inactivo", "baja"] | None = None
    dob: str | None = None                       # fecha ISO (YYYY-MM-DD)

async def propose_update_client(
    question: str, practice_id: str, *, now: datetime, gen_llm=None,
) -> ProposalResult                             # `now` por uniformidad del dispatch (nodes.py:123 siempre lo pasa); no se usa acá
```

### Extracción (`gemma4:12b`, `with_structured_output(ProposedClientUpdate)`)
- Prompt (a mano, español): rol = asistente de datos de clientes; "extraé el cliente y SOLO los datos a
  cambiar entre: teléfono, email, estado (activo/inactivo/baja), fecha de nacimiento (`dob`, YYYY-MM-DD). Si un
  dato no se menciona, dejalo null. 'dar de baja' → `status='baja'`; 'reactivar' → `status='activo'`. **No
  inventes valores.** No extraigas notas ni texto libre." Excepción → `None` → abstención genérica.

### Resolución determinística (sin LLM, scoped por `practice_id`)
1. `resolve_single_client(practice_id, client_name, limit=settings.appt_name_match_limit)`.
2. Recolectar `changes = {campo: valor}` para `phone/email/status/dob` no-`None`. Para `dob`: validar con
   `date.fromisoformat`; **inválido → descartar `dob`** (degrada). Si tras esto `changes` queda **vacío** →
   abstención (`no_fields`, *"¿Qué dato querés cambiar? Puedo teléfono, email, estado o fecha de nacimiento."*).
3. `current = await db.get_client(practice_id, client["id"])` (para el antes→después).
4. `proposed_action`:
   ```python
   {
     "kind": "update_client",
     "summary": "Actualizar Ana López: teléfono 11-1111-1111 → 11-2233-4455",   # un renglón por campo
     "params": {
       "client_id": "…",                            # writer
       "phone": "11-2233-4455",                     # writer (solo los campos a cambiar)
       # email/status/dob ausentes si no cambian
       "client_name": "Ana López",                  # display
       "before": {"phone": "11-1111-1111"},         # display (valores actuales de los campos a cambiar)
     },
   }
   ```
   El `summary` se arma del `before` (de `get_client`) + los valores nuevos; un renglón por campo cambiado.

## Tools parametrizadas — `app/db.py`

```python
async def reschedule_appointment(
    practice_id: str, appointment_id: str, new_start_at: datetime, new_end_at: datetime,
) -> dict[str, Any] | None:
```
```sql
UPDATE appointments SET start_at = $3, end_at = $4
WHERE id = $1 AND practice_id = $2 AND status IN ('programado','confirmado')
RETURNING id::text, start_at, end_at, status;
```
- `None` si 0 filas (turno de otra práctica/inexistente, o ya no reprogramable por estado → idempotencia/TOCTOU,
  simétrico a `cancel_appointment`). Guard `practice_id` = cinturón multi-tenant; guard `status` evita
  reprogramar lo ya cancelado/atendido/ausente. No re-chequea `start_at >= now` (Decisión #4).

```python
async def get_client(practice_id: str, client_id: str) -> dict[str, Any] | None:
```
```sql
SELECT id::text, full_name, phone, email, status, dob::text
FROM clients WHERE id = $1 AND practice_id = $2;
```
- Scopeado por `practice_id`; `None` si no existe/otra práctica. `dob::text` para serializar fácil al display.

```python
async def update_client(
    practice_id: str, client_id: str, *,
    phone: str | None = None, email: str | None = None,
    status: str | None = None, dob: date | None = None,
) -> dict[str, Any] | None:
```
```sql
UPDATE clients SET
  phone  = COALESCE($3, phone),
  email  = COALESCE($4, email),
  status = COALESCE($5, status),
  dob    = COALESCE($6, dob)
WHERE id = $1 AND practice_id = $2
RETURNING id::text, full_name, phone, email, status, dob::text;
```
- `COALESCE($n, col)`: setea solo lo provisto, nunca borra (un `None` mantiene el valor actual). `None`
  (0 filas) si el cliente es de otra práctica/inexistente. El `CHECK (status IN ('activo','inactivo','baja'))`
  del schema valida el enum (defensa en profundidad). Parámetros `$n` (asyncpg), **sin interpolar texto**.

## Registry — `app/agents/write_tools.py`

```python
# ---- reschedule_appointment ----
async def _write_reschedule(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = await db.reschedule_appointment(
        practice_id, params["appointment_id"],
        datetime.fromisoformat(params["new_start_at"]),
        datetime.fromisoformat(params["new_end_at"]),
    )
    return {"rescheduled": True, **row} if row is not None else {"rescheduled": False}

def format_reschedule_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("rescheduled"):
        return "⚠️ No pude reprogramar el turno: ya no estaba disponible (puede haberse cancelado o atendido)."
    start = datetime.fromisoformat(params["new_start_at"])
    return (
        f"✅ Turno reprogramado: {params['client_name']} con {params['practitioner_name']} "
        f"→ {start.strftime('%d/%m %H:%M')} (UTC)."
    )

# ---- update_client ----
async def _write_update_client(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    dob = params.get("dob")
    row = await db.update_client(
        practice_id, params["client_id"],
        phone=params.get("phone"), email=params.get("email"),
        status=params.get("status"), dob=date.fromisoformat(dob) if dob else None,
    )
    return {"updated": True, **row} if row is not None else {"updated": False}

def format_update_client_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("updated"):
        return "⚠️ No pude actualizar al cliente: no lo encontré."
    campos = []
    if params.get("phone"):  campos.append(f"teléfono → {params['phone']}")
    if params.get("email"):  campos.append(f"email → {params['email']}")
    if params.get("status"): campos.append(f"estado → {params['status']}")
    if params.get("dob"):    campos.append(f"fecha de nacimiento → {params['dob']}")
    return f"✅ Datos actualizados de {row['full_name']}: " + "; ".join(campos) + "."

REGISTRY["reschedule_appointment"] = WriteTool(
    kind="reschedule_appointment", propose=propose_reschedule, write=_write_reschedule,
    format_receipt=format_reschedule_receipt, cancel_message="Listo, dejé el turno como estaba.",
)
REGISTRY["update_client"] = WriteTool(
    kind="update_client", propose=propose_update_client, write=_write_update_client,
    format_receipt=format_update_client_receipt, cancel_message="Listo, no cambié los datos del cliente.",
)
```

- Adapters (mismo patrón que `_write_cancel`): mapean `params` al writer, **descartan** claves display
  (`client_name`/`practitioner_name`/`old_start_at`/`before`), parsean ISO→`datetime`/`date`, y envuelven el
  `None` del writer (`{"rescheduled"/"updated": False}`) para que el recibo sea cordial en vez de explotar (el
  writer **no** levanta excepción en el camino "ya no disponible": es resultado esperado).
- **`WRITE_KINDS`** pasa a `("create_appointment", "log_interaction", "cancel_appointment",
  "reschedule_appointment", "update_client", "unsupported")`. Sin solapamiento de substrings entre kinds → el
  match exacto/substring de `classify_write_action` sigue fiable.
- **`CLASSIFY_PROMPT`** (extendido; saca reprogramar y dar-de-baja de `unsupported`; contrasta por verbo y por
  objeto turno-vs-cliente):
  ```
  - create_appointment: agendar/crear un turno NUEVO. Ej: "agendá un turno para Ana mañana 10".
  - log_interaction: registrar/anotar una interacción YA OCURRIDA. Ej: "registrá que llamé a Ana".
  - cancel_appointment: cancelar/anular un turno EXISTENTE. Ej: "cancelá el turno de Juan".
  - reschedule_appointment: REPROGRAMAR/MOVER/cambiar la fecha u hora de un turno EXISTENTE (sigue existiendo,
    cambia cuándo). Ej: "reprogramá el turno de Juan para el jueves", "movés la cita de Ana a las 15",
    "cambiá el turno de Pedro al lunes 11".
  - update_client: editar DATOS del CLIENTE (teléfono, email, estado activo/inactivo/baja, fecha de
    nacimiento). Ej: "cambiá el teléfono de Ana", "actualizá el email de Juan", "dá de baja a Pedro".
  - unsupported: cualquier OTRA acción de escritura (facturar; agregar/editar una NOTA o texto libre de un
    cliente; borrar registros). Ej: "agregá una nota sobre Juan", "facturá la sesión de Ana".
  Respondé solo con la opción.
  ```
- **Sin cambios** en el mecanismo de `classify_write_action` (ya es `ainvoke` + text-parse + retry + fallback
  `unsupported`, por el `None` intermitente del `e4b`, CLAUDE.md §4 / addendum Slices 5/6).

## Nodos del grafo — `app/graph/nodes.py`

Los nodos ya son genéricos por `kind` (Slice 5) → **no cambian su lógica**. Un solo toque:

- **Copy de capacidades** en `propose_action_node` (rama `unsupported`): el mensaje del Slice 6 *"Por ahora
  puedo agendar turnos, registrar interacciones o cancelar turnos. ¿Qué necesitás?"* pasa a *"Por ahora puedo
  agendar, reprogramar o cancelar turnos, registrar interacciones y actualizar datos de clientes (teléfono,
  email, estado). ¿Qué necesitás?"*. (Honestidad de capacidades; copy, no lógica.)

`edges.py`, `build.py`, `state.py`, transporte (`main.py`): **sin cambios**.

## Frontend

**Sin cambios funcionales.** `ConfirmCard.tsx` rinde `action.summary` + los tokens del recibo, agnóstica al
`kind` (ya reusada para `log_interaction` y `cancel_appointment`). El `summary` multi-renglón de `update_client`
(antes→después) y el de `reschedule` (viejo→nuevo) renderizan como texto. Evento SSE `confirm` y `/chat/resume`
no dependen de la tool.

- **Opcional (contrato)**: casos en `ConfirmCard.test.tsx` con `action` de `kind:"reschedule_appointment"` y
  `kind:"update_client"` para fijar que la card sigue genérica. Es test, no producto.

## Config (`config.py`)

**Sin vars nuevas.** Los resolvers reusan `settings.appt_name_match_limit` (ya existe; acota el `LIMIT` del
finder de turnos y el ILIKE de clientes). **Fast-follow re-fichado del Slice 6**: un `appt_resolve_limit`
dedicado para el finder de turnos (hoy comparte el de nombres). Modelos: clasificador `gemma4:e4b`; extracción
`gemma4:12b`; **sin LLM** en resolvers/escrituras/recibos.

## Multi-tenant (CLAUDE.md §0.5)

`practice_id` viaja en `AgentState`. `reschedule`: el resolver (Slice 6) ya filtra por `practice_id`+`client_id`
en el finder, y el `UPDATE` re-verifica `practice_id`. `update_client`: `get_client` y `update_client` filtran
por `practice_id` en el `WHERE`; el cliente se resolvió scopeado. No hay forma de mutar datos de otra práctica.
Pre-RLS el aislamiento es **app-level** (resolver scopeado + guard en cada `UPDATE`); RLS en Postgres = Fase 4.

## Seguridad / guardrails

- **HITL inquebrantable**: sin confirmación explícita, **no hay mutación**. Abstención, `unsupported` y
  "cancelar" (en la tarjeta) no escriben. El `interrupt`/`resume` no recomputa la propuesta (2 nodos, Slice 4).
- **Tools parametrizadas, no SQL libre** (§4): ambos `UPDATE` usan `$n`; el LLM solo produce nombre + pistas +
  valores estructurados, nunca ids ni SQL.
- **PII estructurada vs. texto libre** (§0/§5): `update_client` escribe teléfono/email/estado/dob —PII que **es
  el payload** de la operación, vía tool parametrizada y con confirmación—, pero **no** captura `notes`/texto
  libre (diferido a Guardrails). Tarjeta/recibo muestran los valores al usuario **autorizado**; **los logs
  registran `kind` + `client_id` + nombres de campos cambiados, NO los valores en crudo** (teléfono/email no van
  al log → "el audit log nunca guarda PII en crudo", §5). `reschedule` no captura texto libre nuevo.
- **Idempotencia / TOCTOU**: el guard de `status` en `reschedule_appointment` (y la inexistencia en
  `update_client`) → 0 filas → recibo "⚠️ …", sin efecto. Cubierto en `test_db`.
- **Inyección**: la frase nunca se concatena a SQL; fechas con `datetime.fromisoformat`/`date.fromisoformat`
  (fallan → abstención/descarte), no se ejecutan. Detección de inyección en entrada = Guardrails.

## Testing (DoD CLAUDE.md §6)

Patrón establecido: inyección de `gen_llm=` y `monkeypatch` de funciones de módulo (`test_action_agent.py`,
`test_cancel_agent.py`, `test_interaction_agent.py`, `test_nodes.py`, `test_hitl_cycle.py`, `test_write_tools.py`).

- **No-llm** (sin Ollama):
  - `test_db.py` (extender, integración DB real local):
    - `reschedule_appointment`: mueve `start_at`/`end_at` de un turno `programado`/`confirmado` (devuelve fila,
      `status` intacto); **rechaza** (→ `None`) turno de otra `practice_id`; **rechaza** (→ `None`) turno
      `cancelado`/`atendido`/`ausente` (guard de estado).
    - `get_client`: devuelve el row scopeado; `None` para cliente de otra `practice_id`.
    - `update_client`: update parcial (solo `phone` → email/status/dob intactos, vía `COALESCE`); varios campos
      a la vez; tenant-scoped (→ `None` cliente de otra práctica); el `CHECK` de `status` rige.
  - `test_reschedule_agent.py` (nuevo): `propose_reschedule` con `gen_llm` fake + `db.*`/resolvers
    monkeypatcheados → happy (cliente 1 + turno 1 + nueva fecha) → `proposed_action` con `appointment_id`,
    `new_start_at`/`new_end_at` (duración preservada: `new_end − new_start == old_end − old_start`) y `summary`
    "viejo → nuevo"; extractor `None` → abstención; `new_start_at` ilegible → abstención `datetime_parse_failed`;
    `new_start_at` pasado → abstención `new_time_past`; `current_when` ilegible → degrada (no aborta); cliente
    0/>1 → abstención (mensajes de `resolve_single_client`); turno 0/>1 → abstención del resolver.
  - `test_update_client_agent.py` (nuevo): `propose_update_client` → happy 1 campo y happy multi-campo →
    `proposed_action` con solo los campos cambiados y `summary` antes→después (usa `get_client`
    monkeypatcheado); extractor `None` → abstención; cliente 0/>1 → abstención; **sin campos** → abstención
    `no_fields`; `dob` inválida con otro campo válido → descarta `dob`, propone el resto; `dob` inválida sola →
    abstención `no_fields`.
  - `test_write_tools.py` (extender): `REGISTRY["reschedule_appointment"]` y `["update_client"]` existen y son
    coherentes; `WRITE_KINDS` los incluye; `classify_write_action` con `llm` fake rutea "reprogramá el turno de
    X" → `reschedule_appointment`, "cambiá el teléfono de X" / "dá de baja a X" → `update_client`, "agregá una
    nota sobre X" → `unsupported`, y create/cancel/log siguen a su kind (no-regresión del clasificador);
    `_write_reschedule` y `_write_update_client` con `db.*` monkeypatcheado → fila → `{"rescheduled"/"updated":
    True,…}`, `None` → `{…: False}`; receipts ramas ok ("✅ …") y no-ok ("⚠️ …"); el recibo de `update_client`
    lista solo los campos cambiados.
  - `test_nodes.py` (extender): copy de capacidades de `propose_action_node` menciona "reprogramar" y
    "actualizar datos de clientes"; `confirm_action_node` parametrizado con `kind` reschedule/update_client:
    confirm → `tool.write` 1 vez con `params` + recibo "✅"; cancel → `tool.write` **no** se llama + `cancel_message`.
  - `test_hitl_cycle.py` (extender/parametrizar): ciclo `interrupt`→`resume` para `reschedule_appointment` y
    `update_client` (`MemorySaver`, `propose_*` monkeypatcheado a un `ProposalResult` ya resuelto) →
    `resume="confirm"` → `write` espiado 1 vez; `resume="cancel"` → no se llamó.
  - **No-regresión**: los tests de `create_appointment`/`log_interaction`/`cancel_appointment` siguen verdes sin
    cambiar asertos. `test_resolvers.py` no cambia (`resolve_single_appointment` se reusa sin tocar).
- **`-m llm`** (Ollama + Postgres reales, `seed_demo.py` corrido):
  - `test_reschedule_e2e_llm.py` (nuevo): seed cliente único + turno futuro `programado`; *"reprogramá el turno
    de \<nombre\> para mañana a las 15"* → `ainvoke` → interrupt (`kind=="reschedule_appointment"`,
    `appointment_id` + `new_start_at` poblados); `resume="confirm"` → `start_at`/`end_at` actualizados,
    `status` intacto (verificado por `id`); `resume="cancel"` → turno intacto. Aserto de **clasificación**:
    reprogramar clasifica `reschedule_appointment` (no `cancel`/`create` pese a compartir "turno").
  - `test_update_client_e2e_llm.py` (nuevo): seed cliente único; *"cambiá el teléfono de \<nombre\> a
    11-9999-0000"* → interrupt (`kind=="update_client"`, `params["phone"]` poblado); `resume="confirm"` →
    `clients.phone` actualizado (verificado por `id`); `resume="cancel"` → intacto. Limpieza: usar cliente/turno
    creados ad-hoc o re-`UPDATE` para no dejar el seed mutado entre corridas.
- **Frontend** (`vitest`): opcional, casos de contrato `ConfirmCard` para ambos kinds. Verde: vitest + lint +
  build (sin cambios de producto). (Correr con `npm --prefix frontend run test -- --run`.)
- **Gates**: `ruff check . && ruff format .`; `mypy --config-file backend/pyproject.toml` (siempre con la
  config: sin ella, falso-positivo `asyncpg [import-untyped]`); `pytest -q` (no-llm) verde. **Smoke §2**:
  *"reprogramá el turno de \<cliente\> …"* → **abre tarjeta** → Confirmar → ✅ + `start_at`/`end_at` nuevos en
  DB; *"cambiá el teléfono de \<cliente\> …"* → tarjeta antes→después → Confirmar → ✅ + `phone` nuevo en DB;
  Cancelar → intacto; ambiguo → abstiene listando; y las tres tools previas siguen abriendo tarjeta y
  escribiendo (no-regresión).

## Dependencias

Ninguna nueva. `langgraph` ya provee `interrupt`/`Command` (checkpointer Postgres cableado en el `lifespan`);
`with_structured_output` ya se usa; el registry, `resolve_single_appointment`/`resolve_single_client` y la
`ConfirmCard` ya existen. Sin red saliente fuera de Ollama/Postgres/Qdrant locales (DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff`, `mypy --config-file backend/pyproject.toml`, `pytest -q` (no-llm) verdes; `-m llm` verde con Ollama +
   ambos modelos + Postgres + `seed_demo.py` corrido.
2. Tocamos el grafo (vía registry) y agregamos dos tools de escritura: smoke §2 pasa, **las escrituras piden
   confirmación de verdad**, y las tres tools previas **no regresionan**. El ciclo `interrupt`→`resume` tiene
   test no-llm para los dos nuevos `kind`.
3. No se tocó retrieval/SQL/síntesis ni el **prompt del router** → la suite offline de eval no aplica. El
   **clasificador de write-actions** cambió (dos kinds + ejemplos): si el e2e mostrara un fallo de
   clasificación, se agrega un caso golden — anotado.
4. Prompts (clasificador extendido + extractores) a mano ahora; recompilar con DSPy = Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales.
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **Clasificación errónea entre las tres ops de turno** (create/cancel/reschedule comparten "turno/cita"):
  riesgo medio — se mitiga con contraste por verbo (agendá/reservá=nuevo vs. cancelá/anulá=anular vs.
  reprogramá/movés/cambiá-fecha=mover) y "nuevo vs. existente-que-sigue-existiendo" en el prompt; text-parse +
  retry + fallback `unsupported`; aserto de clasificación en el e2e; caso golden si aparece un fallo.
- **`update_client` vs. `cancel_appointment`** ("dá de baja a Pedro" vs. "cancelá el turno de Pedro"): se
  contrasta por **objeto** (cliente vs. turno) en los ejemplos del prompt. Riesgo bajo-medio.
- **Extracción de doble fecha en `reschedule`** (cuál es la actual vs. la nueva): se mitiga con ejemplos
  explícitos ("del X al Y" → actual=X, nueva=Y; una sola fecha → nueva). Si el modelo confunde y resuelve un
  turno que no es, el **antes→después de la tarjeta** lo hace visible antes de confirmar; si queda ambiguo,
  abstiene listando (fail-closed).
- **`update_client` parcial / multi-campo**: `COALESCE` solo setea lo provisto (no borra); cubierto en `test_db`
  y `test_update_client_agent`. La recolección de campos no-`None` evita updates vacíos (abstención `no_fields`).
- **PII estructurada en logs**: mitigado por diseño — los logs guardan `kind`+`client_id`+nombres de campos, no
  los valores; los valores solo viajan a la tarjeta/recibo del usuario autorizado. Redacción de PII en
  entrada/salida (incl. `notes` libre) = slice de Guardrails.
- **TOCTOU / reprogramar lo ya no disponible**: guard de `status` en el `UPDATE` → 0 filas → recibo cordial
  "⚠️ …", sin efecto. Cubierto en `test_db`.
- **`with_structured_output` para los dos nuevos modelos**: bajo riesgo (args tipados chicos funcionan en el
  12B; `ProposedClientUpdate` tiene un `Literal`, que la decodificación restringida respeta). Fallback: `format=`
  JSON Schema de Ollama (§4) — anotado, no esperado.
- **Mover a un horario ocupado** (sin chequeo de solapamiento): aceptado como no-objetivo del MVP (igual que
  `create_appointment`); fast-follow.
- **Aislamiento tenant pre-RLS**: app-level (resolvers scopeados + guard `practice_id` en cada `UPDATE`); RLS en
  Fase 4 es el cierre real.
