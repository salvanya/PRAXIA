# Praxia · Fase 1 · Slice 8 — Memoria de corto plazo (`thread_id` estable) + slot-filling de desambiguación (cliente y turno)

> Diseño aprobado el 2026-06-30. Spec de un único slice implementable (grande; el plan lo descompone en tasks incrementales).
> Contrato operativo: `CLAUDE.md`. Diseño completo del producto: `Praxia_Blueprint.md` (§3.2 grafo, §4.2 memoria, §6 Fase 1).
> Slices previos (todos mergeados a `main`): grafo + router (`2026-06-25-grafo-router-design.md`),
> CRAG (`2026-06-26-crag-design.md`), NL2SQL read-only (`2026-06-26-nl2sql-data-agent-design.md`),
> `create_appointment` HITL (`2026-06-27-write-appointment-hitl-design.md`),
> `log_interaction` + registry (`2026-06-28-log-interaction-design.md`),
> `cancel_appointment` (`2026-06-29-cancel-appointment-design.md`),
> `reschedule_appointment` + `update_client` (`2026-06-29-reschedule-and-update-client-design.md`).

## Objetivo

Dar a Praxia **memoria de corto plazo real** y, sobre ella, **slot-filling multi-turno** para resolver la
ambigüedad que hoy obliga a abstenerse. Son dos piezas acopladas: el slot-filling **no existe sin** un
`thread_id` estable que persista el estado entre turnos.

1. **`thread_id` estable end-to-end.** Hoy `/chat` mintea `uuid4()` por request (`main.py:113`) → cada
   mensaje es un thread nuevo y el checkpointer Postgres (ya cableado en el `lifespan`) nunca reusa el estado.
   El front pasa a generar un `thread_id` estable por conversación y mandarlo en cada `/chat`; el backend lo
   respeta y **anexa** el turno al historial del checkpoint en vez de pisarlo.
2. **Memoria conversacional.** Con el historial acumulado, `chitchat` ve los últimos turnos (charla
   multi-turno: recuerda lo dicho antes). RAG/SQL **no** cambian (el follow-up contextual de RAG/SQL es
   fuera de alcance, ver No-objetivos).
   > *Nota de implementación (Slice 8):* el `ROUTER_PROMPT` SÍ se extendió en 2 líneas — la descripción de
   > `chitchat` ahora cubre meta-preguntas conversacionales ("¿qué te dije?", "¿lo recordás?") — porque sin
   > eso esas preguntas ruteaban a `out_of_scope` y la memoria conversacional no funcionaba end-to-end. Cambio
   > mínimo, sin regresión (`test_router.py` + gate no-llm verdes). El **mecanismo** del router no cambió.
3. **Slot-filling de desambiguación.** Cuando una write-tool encuentra ambigüedad, el sistema **pregunta
   «¿cuál?» numerando los candidatos** y **resuelve en el turno siguiente** (en vez de abstenerse listando).
   Cubre los **dos** tipos de ambigüedad que hoy existen:
   - **Turno** (`cancel_appointment`, `reschedule_appointment`): cliente con varios turnos futuros.
   - **Cliente** (las **5** write-tools): nombre que matchea varios clientes.

Entregable observable (todo dentro de **un mismo `thread_id`**):

- *"cancelá el turno de María"* → hay dos «María» → **«Hay varios clientes: 1. María González · 2. María
  Pérez. ¿Cuál?»** (numerado, sin tarjeta). → *"la González"* → María González tiene dos turnos → **«Tiene
  varios turnos: 1. lun 01/07 14:00 con Dra. Gómez · 2. jue 13/07 10:00 con Dr. Ruiz. ¿Cuál?»** → *"el del
  lunes"* → **tarjeta de confirmación** del turno correcto → Confirmar → ✅ cancelado.
- *"hola, soy el Dr. Pérez"* → chitchat → *"¿cómo me presenté?"* → **recuerda** «Dr. Pérez» (memoria
  conversacional, mismo thread).
- Respuesta que **no mapea** a ningún candidato (el usuario tipea otra cosa / cambia de tema) → se **descarta**
  la aclaración pendiente y se pide reintento *(«No identifiqué cuál; volvé a pedírmelo indicando la fecha o el
  nombre completo.»)*; el turno siguiente rutea normal (fail-safe, **no** se adivina).
- Flujo **one-shot** sigue intacto: *"cancelá el turno de Ana del martes"* con un único match resuelve directo
  → tarjeta (sin preguntar). Las **5** write-tools y RAG/SQL/chitchat **no regresionan**.

Gate que cierra el slice (CLAUDE.md §2/§6): el smoke completa una **cadena cliente→turno→confirmación** en un
solo `thread_id`, `chitchat` recuerda un dato entre turnos, **ninguna escritura ocurre sin confirmación**, y
los flujos one-shot de las 5 tools siguen verdes.

## No-objetivos (diferidos, cada uno trabajo propio)

- **Follow-up contextual de SQL/RAG** (*"¿cuántos turnos esta semana?"* → *"¿y la que viene?"*): requiere
  reescritura de consulta con historial (toca Data Agent / CRAG). Fue la **opción no elegida** del alcance.
  RAG/SQL siguen one-shot sobre el último mensaje. Su propio slice.
- **`running_summary` incremental** (resumen de turnos viejos para presupuesto de tokens, CLAUDE.md §4): este
  slice usa una **ventana fija** de los últimos N mensajes en `chitchat`. El resumen incremental es Fase 2
  (caching / context management). El campo `running_summary` sigue **sin** declararse.
- **Persistencia cross-reload del front**: el `thread_id` es **efímero** (vive en memoria del cliente; recargar
  la página = conversación nueva). Persistir en `localStorage` + **rehidratar** el historial desde el backend
  al montar (y un botón "Nueva conversación") = canvas rico de Fase 1, diferido.
- **Slot-filling de PROFESIONAL en `create_appointment`**: `propose_appointment` tiene un **tercer** tipo de
  ambigüedad (profesional: `practitioner_ambiguous` / `practitioner_unspecified`). El mecanismo de este slice
  lo soportaría con un `stage="practitioner"`, pero queda **fuera** (cliente y turno son los pedidos). Sigue
  abstinéndose como hoy. Extensión natural anotada.
- **Memoria de largo plazo / reflexión** (perfiles, hechos persistidos en Qdrant, CLAUDE.md §4 / Blueprint
  §4.2): Fase 2. Este slice es **solo corto plazo** (checkpointer por `thread_id`).
- **Compilar prompts con DSPy** (el nuevo `resolve_choice` y los prompts tocados se escriben a mano): Fase 2.
- **Auth real / `created_by` / `agent_runs` / `consents`**: siguen como en Slices 4–7 (Fase 4).

## Principio rector

Local-first · $0 · privacidad por defecto (CLAUDE.md §0). **No se reimplementa memoria**: el corto plazo es el
**checkpointer Postgres por `thread_id`** que ya está cableado (CLAUDE.md §4 — *"memoria corto plazo =
checkpointer Postgres … gratis, no la reimplementes"*). El grafo sigue siendo la fuente de control: el
slot-filling se enchufa como un **entry condicional** y un **nodo nuevo** (`clarify`) **antes** del router, sin
que ningún camino esquive guardrails/HITL. Inferencia 100% local: el mapeo respuesta→candidato lo hace
`gemma4:12b` con salida estructurada (índice acotado); **no hay LLM** en el ruteo del entry ni en la escritura.
**Fail-closed**: ante una respuesta que no mapea con seguridad, se descarta la aclaración y se pide reintento;
**nunca** se elige un candidato por el usuario. Aislamiento por `practice_id` en toda resolución; el
`pending_clarification` vive en el estado del `thread_id` (scoped).

## Arquitectura

### Decisión de límites #1 — `thread_id` estable: el front es la fuente del id; el backend deja de mintar

- **Front:** genera un `thread_id` (`crypto.randomUUID()`) **una vez por montaje** del chat (en un `useRef`),
  estable mientras dure la conversación, y lo manda en cada `/chat`. Efímero a propósito (recargar = conversación
  nueva; ver No-objetivos).
- **Backend (`main.py`):** `ChatRequest` gana `thread_id: str | None`. `/chat` usa `req.thread_id` (fallback
  `uuid4()` si viniera vacío → comportamiento legacy: thread efímero por request, sin memoria). El **id de
  conversación y el id del ciclo confirm/resume se unifican**: el evento SSE `confirm` ya emite el `thread_id`
  usado, y `/chat/resume` lo reusa (sin cambios en ese transporte).

### Decisión de límites #2 — input incremental vs. inicial (no pisar el estado persistido)

Hoy `/chat` siempre pasa `new_state(...)` completo al grafo: un dict con **todos** los campos reseteados
(`proposed_action=None`, `pending_clarification=None`, etc.). Con `thread_id` estable eso **pisaría** el estado
del checkpoint en cada turno (incluida una aclaración pendiente). Solución: **`/chat` consulta el estado del
thread y elige el input**:

```python
thread_id = req.thread_id or str(uuid4())
config = {"configurable": {"thread_id": thread_id}}
snapshot = await graph.aget_state(config)           # requiere checkpointer (lifespan); fallback → vacío
if snapshot.values:                                 # el thread ya existe → input INCREMENTAL
    inp: Any = {"messages": [HumanMessage(content=req.message)]}
else:                                               # primer turno → input INICIAL completo
    inp = new_state(req.message, practice_id=s.practice_id, thread_id=thread_id)
```

- `messages` usa el reducer `add_messages` → el input incremental **anexa** el `HumanMessage` al historial; el
  resto del estado (`pending_clarification`, `practice_id`, …) **persiste** del checkpoint.
- Campos opcionales (`pending_clarification`, `proposed_action`) se leen **siempre con `.get()`** (en el primer
  turno con input incremental no estarían; con `new_state` se inicializan a `None`).
- **Alternativa descartada:** input incremental siempre + defaults en cada nodo → obliga a auditar **todos** los
  accesos `state["x"]`; la consulta de checkpoint es explícita y localizada en un punto.
- **Fallback sin checkpointer** (`get_default_graph`, usado por algunos tests / si el `lifespan` no corrió):
  `aget_state` devuelve `values` vacío → siempre input inicial → sin memoria (idéntico a hoy). Aceptable.

### Decisión de límites #3 — slot-filling **unificado por re-invocación con overrides** (approach elegido)

Desambiguar **cliente** NO es terminal: tras fijar el cliente hay que **continuar** el flujo de la tool
(resolver el turno, que puede ser **otra** ambigüedad). Desambiguar **turno** sí es terminal. Para cubrir ambos
con un solo mecanismo y **sin** factorizar los 5 agents:

> El `clarify_node` mapea la respuesta → candidato y **vuelve a invocar el mismo `propose_X`**, pasándole el
> slot ya resuelto como **override** (`client_override` / `appointment_override`). El `propose_X` salta el paso
> overrideado y corre el resto igual. Su resultado se procesa **idéntico** a un propose normal: puede devolver
> **otra** aclaración (turno, tras fijar cliente) → encadena; o el `proposed_action` final → confirma.

- **Cambio mínimo por agent**: un `if override is not None` al inicio de cada resolución; **reusa** toda la
  lógica posterior (parseo de fecha, construcción del `proposed_action`). El encadenado cliente→turno sale
  **gratis** porque el segundo `propose` re-resuelve el turno con el cliente ya fijo.
- **Re-extracción**: el `propose` re-invocado re-extrae del `question` **original** (guardado en
  `pending_clarification`). Con `temperature=0` y el mismo input es estable; el único cambio es el override.
- **Alternativas descartadas:** (A2) factorizar cada `propose` en pasos discretos y avanzar slot por slot →
  refactor estructural de los 5 agents, más superficie de test, innecesario si la re-invocación encadena.
  (A3) router LLM "context-aware" que decida si el mensaje es una aclaración → mete la decisión en el `e4b`,
  que da `None` intermitente (gotcha vigente); el entry **determinístico** (mirar `pending_clarification`) es
  más robusto y barato.

### Decisión de límites #4 — el entry del grafo bifurca **antes** del router (no se toca el router LLM)

```
START ─┬─ (pending_clarification?) ─► clarify   ─► (proposed_action? confirm_action : END)
       └─ (else)                    ─► router    ─► … (igual que hoy)
```

`entry_route(state) -> "clarify" if state.get("pending_clarification") else "router"`. El router (e4b) **no
cambia** (sigue grueso, 5 intents, su prompt no crece). Esto evita el riesgo de meter el estado de diálogo en
el clasificador inestable.

### Decisión de límites #5 — los resolvers exponen **candidatos estructurados**; `ProposalResult` gana `clarification`

Hoy los resolvers, ante ambigüedad, devuelven **solo un mensaje de texto** con la lista (no los candidatos con
sus IDs). El slot-filling necesita los candidatos estructurados para (a) numerarlos y (b) reconstruir el
`proposed_action` tras elegir.

- `ClientResolution` y `AppointmentResolution` ganan `candidates: list[dict[str, Any]]` (poblado con los
  matches cuando el `reason` es `*_ambiguous`; vacío si no).
- `ProposalResult` gana `clarification: Clarification | None`, **distinto** de la abstención dura:
  - `proposed_action is not None` → resolvió → confirmar.
  - `clarification is not None` → **ambigüedad slot-filleable** → abrir/continuar diálogo (setear `pending`).
  - `abstained and clarification is None` → **abstención dura** (no encontrado, falta dato, extractor falló) →
    mensaje cordial, sin diálogo (idéntico a hoy).

```python
@dataclass
class Clarification:
    stage: str                         # "client" | "appointment"
    candidates: list[dict[str, Any]]   # estructurados, con ids, ya scoped por practice_id
    prompt: str                        # encabezado humano ("Hay varios clientes…" / "Tiene varios turnos…")
```

### Decisión de límites #6 — unificar `propose_appointment` sobre `resolve_single_client` (ejecuta fast-follow fichado)

`propose_appointment` (create) tiene hoy un resolver de cliente **inline** (`action_agent.py:94-109`), no usa
`resolve_single_client`. Para que `create_appointment` participe del slot-filling de **cliente** como las otras
4, se ejecuta el fast-follow ya fichado: **reemplazar el bloque inline por `resolve_single_client`** (que ahora
expone `candidates`). El resolver de **profesional** de create queda **como está** (abstención dura; el
slot-filling de profesional es No-objetivo).

### Decisión de límites #7 — un nodo nuevo (`clarify`) + un agente nuevo (`resolve_choice`); el resto, cableado

```
backend/app/
├── graph/
│   ├── state.py          # +pending_clarification: dict | None  (new_state lo inicializa a None)
│   ├── nodes.py          # +clarify_node; chitchat_node ve el historial; _handle_proposal_result compartido
│   ├── edges.py          # +entry_route; route_after_clarify (= route_after_propose)
│   └── build.py          # +nodo "clarify"; entry condicional reemplaza add_edge(START,"router")
├── agents/
│   ├── choice_agent.py   # NUEVO: resolve_choice (mapea respuesta→índice de candidato; 12b structured)
│   ├── resolvers.py      # ClientResolution/AppointmentResolution +candidates
│   ├── action_agent.py   # propose_appointment: usa resolve_single_client; acepta client_override
│   ├── cancel_agent.py   # propose_cancellation: acepta client_override, appointment_override
│   ├── reschedule_agent.py # propose_reschedule: idem cancel
│   ├── interaction_agent.py# propose_interaction: acepta client_override
│   ├── update_client_agent.py # propose_update_client: acepta client_override
│   └── write_tools.py    # firma de propose en el descriptor admite overrides (kwargs)
├── main.py               # ChatRequest.thread_id; /chat elige input incremental/inicial
└── config.py             # +short_term_history_window: int = 10
frontend/
├── lib/chatStream.ts     # streamChat(message, threadId): body {message, thread_id}
└── lib/runtime.ts        # genera/mantiene threadId estable (useRef) y lo pasa
```

Regla CLAUDE.md §3: un nodo = una función pura/testeable; la lógica nueva (mapeo, manejo del resultado) vive en
`agents/` y en helpers, no incrustada en los nodos.

## Flujo de datos (cadena cliente→turno, todo en un `thread_id`)

```
 Turno 1: "cancelá el turno de María"        (thread_id estable; practice_id, now=UTC)
   START ──(pending? NO)──► router (intent=action) ──► propose_action
        classify_write_action → "cancel_appointment"
        REGISTRY["cancel_appointment"].propose(question, practice_id, now)      ← sin overrides
           12b → ProposedCancellation{client_name:"María", when:None}
           resolve_single_client("María") → 2 matches → Clarification(stage="client",
                                                          candidates=[G,P], prompt="Hay varios clientes…")
   _handle_proposal_result → pending_clarification = {kind, stage:"client", candidates:[G,P],
                                                      question:"cancelá…María", overrides:{}}
   write_token("Hay varios clientes: 1. María González · 2. María Pérez. ¿Cuál?")  → END

 Turno 2: "la González"
   START ──(pending? SÍ)──► clarify_node
        resolve_choice(numbered([G,P]), "la González") → 1   (12b, Literal int; 0=no claro)
        overrides = {client: G}
        REGISTRY["cancel_appointment"].propose(question_orig, …, client_override=G)
           re-extrae when=None; cliente=G (override, salta resolve_single_client)
           resolve_single_appointment(G, when=None) → 2 turnos → Clarification(stage="appointment",
                                                                 candidates=[t1,t2], prompt="Tiene varios…")
   _handle_proposal_result → pending = {kind, stage:"appointment", candidates:[t1,t2],
                                        question:"cancelá…María", overrides:{client:G}}
   write_token("Tiene varios turnos: 1. lun 01/07 14:00 … · 2. jue 13/07 10:00 … ¿Cuál?")  → END

 Turno 3: "el del lunes"
   START ──(pending? SÍ)──► clarify_node
        resolve_choice(numbered([t1,t2]), "el del lunes") → 1
        overrides = {client:G, appointment:t1}
        REGISTRY["cancel_appointment"].propose(question_orig, …, client_override=G, appointment_override=t1)
           cliente=G (override); turno=t1 (override) → proposed_action{kind, summary, params{appointment_id…}}
   _handle_proposal_result → proposed_action set, pending=None
   route_after_clarify → confirm_action → interrupt(proposed_action) ⏸ → SSE `confirm` (tarjeta)
        usuario Confirmar → /chat/resume → db.cancel_appointment → "✅ Turno cancelado: …"   → END

 Caso no-mapea (cualquier turno con pending): resolve_choice → 0 (o índice fuera de rango)
   → pending=None; write_token("No identifiqué cuál; volvé a pedírmelo indicando la fecha o el nombre completo.") → END
```

Reglas del flujo:
- **Determinístico el ruteo del entry**: mirar `pending_clarification`, sin LLM.
- **Cada ronda fija un slot** (cliente→turno→done) → converge; el no-mapea corta. Sin loops.
- **El LLM nunca toca IDs**: `resolve_choice` devuelve un **índice**; el `proposed_action` se arma con los
  candidatos estructurados (de la DB, scoped). El override que viaja al `propose` es el dict del candidato
  resuelto, no texto del usuario.
- **`confirm_action` / `ConfirmCard` / `/chat/resume`: sin cambios** (dividendo del registry de Slice 5).

## Estado del grafo — `app/graph/state.py`

```python
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    practice_id: str
    thread_id: str
    intent: str
    retrieved: list[Chunk]
    sources: list[dict]
    candidate_sql: str
    judge_scores: dict
    proposed_action: dict | None
    pending_clarification: dict | None      # NUEVO

# new_state(...) agrega:  "pending_clarification": None
```

`pending_clarification` (dict, serializable por el checkpointer):
```python
{
  "kind": "cancel_appointment",            # write-tool en curso
  "stage": "client" | "appointment",       # slot que se desambigua AHORA
  "candidates": [ {...}, {...} ],            # clients o appointments estructurados (con ids), scoped
  "question": "cancelá el turno de María",  # pedido ORIGINAL (para re-invocar propose)
  "overrides": { "client": {...}? , "appointment": {...}? },  # slots fijados en rondas previas
}
```

## Mapeo respuesta→candidato — `app/agents/choice_agent.py` (NUEVO)

```python
class Choice(BaseModel):
    choice: int                              # 1..N elegido; 0 = no está claro

async def resolve_choice(numbered: str, reply: str, *, gen_llm=None) -> int:
    """Mapea la respuesta del usuario a UN índice de candidato (1..N) o 0 si no es claro.
    12b + with_structured_output(Choice): el índice es un entero acotado (como un id/enum),
    confiable en el 12B. Fail-closed: excepción / fuera de rango → 0 (no-mapea)."""
```

- Prompt (a mano, español): *"Te doy una lista numerada de opciones y la respuesta del usuario. Devolvé el
  número de la opción que eligió. Si la respuesta no identifica con claridad UNA opción, devolvé 0. No
  inventes."* + la lista `numbered` + la `reply`.
- El **rango** se valida en código: `1 <= choice <= len(candidates)` si no, `0`. (El prompt pide 0, pero el
  guard de rango es la red.) Conservador a propósito: el HITL es la **última** red, no la única.
- Modelo `gemma4:12b` (no `e4b`): el structured-output de enteros del 12B es confiable (mismo patrón que los
  extractores tipados); además mapear *"el del lunes"* a un candidato exige razonar sobre las fechas de la
  lista, que el 12B hace mejor.

## Resolvers — `app/agents/resolvers.py`

```python
@dataclass
class ClientResolution:
    client: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str
    candidates: list[dict[str, Any]]         # NUEVO: matches cuando reason=="client_ambiguous"; [] si no

@dataclass
class AppointmentResolution:
    appointment: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str
    candidates: list[dict[str, Any]]         # NUEVO: matches cuando reason=="appointment_ambiguous"; [] si no
```

- `resolve_single_client`: en la rama `len(clients) > 1` setea `candidates=clients` (hoy solo arma el texto).
- `resolve_single_appointment`: en la rama `len(matches) > 1` setea `candidates=matches`. (El resto de ramas
  y la heurística de `when` de Slice 6 **no cambian**.)
- El **numerado** NO lo hace el resolver (sigue agnóstico a la presentación). La capa de grafo numera con un
  helper (`_format_candidate` ya existe para turnos; para clientes es `full_name`).

## Agentes (write-tools) — overrides

Firma uniforme para el dispatch (todos aceptan ambos overrides; los irrelevantes ignoran `appointment_override`,
patrón ya usado: `propose_update_client` acepta `now` sin usarlo):

```python
async def propose_X(
    question: str, practice_id: str, *, now: datetime, gen_llm=None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,   # solo cancel/reschedule lo usan
) -> ProposalResult
```

- **Cliente** (`cancel`, `reschedule`, `interaction`, `update_client`, y `appointment` tras unificar): si
  `client_override is not None` → `client = client_override` (salta `resolve_single_client`). Si no, resuelve;
  ante `client_ambiguous` → devuelve `ProposalResult(clarification=Clarification("client", res.candidates,
  "Hay varios clientes que coinciden con «…»"))`. Las demás ramas de abstención dura quedan igual.
- **Turno** (`cancel`, `reschedule`): si `appointment_override is not None` → `appt = appointment_override`
  (salta `resolve_single_appointment`). Si no, resuelve; ante `appointment_ambiguous` →
  `ProposalResult(clarification=Clarification("appointment", res.candidates, "…tiene varios turnos…"))`.
- `reschedule` preserva su lógica de `new_start_at`/`new_end_at` (duración) tal cual; el override solo fija el
  turno objetivo (igual que el resolver lo fijaría).
- **`propose_appointment`** (D#6): el bloque inline de cliente (`action_agent.py:94-109`) se reemplaza por
  `resolve_single_client(...)`; el comportamiento (mensajes de no-encontrado/ambiguo) se preserva y ahora
  expone `candidates`. El resolver de **profesional** queda intacto (abstención dura).
- `ProposalResult` (en `action_agent.py`, lo importan los demás) gana `clarification: Clarification | None = None`.

## Nodos del grafo — `app/graph/nodes.py`

### `_handle_proposal_result` (helper compartido por `propose_action_node` y `clarify_node`)

```python
def _handle_proposal_result(result, *, kind, question, overrides) -> dict:
    if result.clarification is not None:
        clar = result.clarification
        pending = {"kind": kind, "stage": clar.stage, "candidates": clar.candidates,
                   "question": question, "overrides": overrides}
        write_token(_numbered_prompt(clar))          # "…: 1. … · 2. … ¿Cuál?"
        write_sources([])
        return {"pending_clarification": pending, "sources": [], "messages": [AIMessage(content=...)]}
    if result.abstained:                              # abstención dura
        write_token(result.message); write_sources([])
        return {"pending_clarification": None, "proposed_action": None, "sources": [],
                "messages": [AIMessage(content=result.message)]}
    return {"pending_clarification": None, "proposed_action": result.proposed_action}  # → confirm
```

### `propose_action_node` (ajuste)
Tras `classify_write_action` y `REGISTRY[kind].propose(question, practice_id, now=…)` (sin overrides), delega en
`_handle_proposal_result(result, kind=kind, question=question, overrides={})`. La rama `unsupported` /
`kind not in REGISTRY` (copy de capacidades) **no cambia**.

### `clarify_node` (NUEVO)
```python
async def clarify_node(state) -> dict:
    pending = state["pending_clarification"]
    reply = last_user_text(state)
    idx = await resolve_choice(_numbered(pending["candidates"], pending["stage"]), reply)
    if not (1 <= idx <= len(pending["candidates"])):           # no-mapea → limpiar + reintento
        msg = "No identifiqué cuál; volvé a pedírmelo indicando la fecha o el nombre completo."
        write_token(msg); write_sources([])
        return {"pending_clarification": None, "sources": [], "messages": [AIMessage(content=msg)]}
    chosen = pending["candidates"][idx - 1]
    overrides = {**pending["overrides"], pending["stage"]: chosen}
    result = await REGISTRY[pending["kind"]].propose(
        pending["question"], state["practice_id"], now=datetime.now(UTC),
        client_override=overrides.get("client"), appointment_override=overrides.get("appointment"))
    return _handle_proposal_result(result, kind=pending["kind"],
                                   question=pending["question"], overrides=overrides)
```

### `chitchat_node` (ajuste — memoria conversacional)
Pasa los **últimos N mensajes** del historial (no solo `last_user_text`), con `N =
settings.short_term_history_window`. Convierte cada mensaje a `("human"|"ai", content)` (descartando vacíos /
mensajes sin texto). El `CHITCHAT_SYSTEM` se mantiene como primer turno del prompt. Los **demás nodos**
(`rag`, `sql`, `propose_action`) siguen usando `last_user_text` (no se vuelven contextuales — No-objetivo).

### `confirm_action_node` (higiene, opcional)
Al terminar (confirm o cancel) devuelve además `"proposed_action": None` para no dejar la propuesta consumida
en el estado del thread persistente. 1 línea, bajo riesgo.

## Edges y build — `app/graph/edges.py`, `app/graph/build.py`

```python
# edges.py
def entry_route(state: AgentState) -> str:
    return "clarify" if state.get("pending_clarification") else "router"

route_after_clarify = route_after_propose      # mismo predicado: confirm_action si proposed_action else END

# build.py
g.add_node("clarify", clarify_node)
g.add_conditional_edges(START, entry_route, {"clarify": "clarify", "router": "router"})   # reemplaza add_edge(START,"router")
g.add_conditional_edges("clarify", route_after_clarify, {"confirm_action": "confirm_action", END: END})
# (router, propose_action, leaf nodes: igual que hoy)
```

## Transporte — `app/main.py`

```python
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None            # NUEVO (opcional; fallback uuid4 = legacy efímero)

# /chat: ver Decisión #2 (aget_state → input incremental vs new_state inicial).
```

- `from langchain_core.messages import HumanMessage` (para el input incremental). `new_state` se sigue usando
  para el primer turno.
- `/chat/resume`: **sin cambios** (ya recibe `thread_id` del front; ahora ese id es el estable de la
  conversación, lo que unifica el ciclo).

## Frontend — `frontend/lib/`

- `chatStream.ts`: `streamChat(message, threadId, signal)` → `body: { message, thread_id: threadId }`. El parse
  de eventos y `resumeChat` no cambian.
- `runtime.ts`: `useChatRuntime` crea un `threadId` estable con `useRef(crypto.randomUUID())` (una vez por
  montaje) y lo pasa a `streamChat(query, threadId, abortSignal)`. El `threadId` que llega en el evento
  `confirm` coincide con el estable → `resume` sigue igual. `ConfirmCard.tsx`: **sin cambios**.
- Memoria conversacional del **render**: `@assistant-ui/react` (`useLocalRuntime`) ya mantiene el historial
  local para la UI; la **verdad** de la sesión del grafo es el checkpointer. No se rehidrata cross-reload
  (No-objetivo).

## Config — `app/config.py`

```python
short_term_history_window: int = 10        # mensajes recientes que ve chitchat (ventana fija; running_summary = Fase 2)
```
Sin otras vars. `appt_name_match_limit` (ya existe) acota el nº de candidatos (≤5) → el `Choice.choice` cae en
rango chico. Modelos: `resolve_choice` y extractores `gemma4:12b`; clasificador/router `gemma4:e4b`; **sin LLM**
en entry/escritura.

## Multi-tenant (CLAUDE.md §0.5)

`practice_id` viaja en `AgentState`. Los `candidates` salen de los resolvers, que filtran por `practice_id` (y
`client_id` para turnos) → un candidato de otra práctica **no** se ofrece ni se puede elegir. El re-invocado
`propose` recibe `practice_id` y los overrides ya scoped. `pending_clarification` vive en el estado del
`thread_id` (aislado por thread). El `UPDATE`/`INSERT` finales re-verifican `practice_id` (Slices 4–7, sin
cambios). Pre-RLS el aislamiento es app-level; RLS = Fase 4.

## Seguridad / guardrails

- **HITL inquebrantable**: el slot-filling solo **resuelve cuál**; la escritura sigue **detrás del `interrupt`**.
  Ninguna rama del `clarify` escribe; solo arma `proposed_action` → `confirm_action`. El no-mapea no escribe.
- **El LLM no elige por el usuario**: `resolve_choice` es conservador (0 si dudoso) + guard de rango; ante duda
  se pide reintento. El HITL es la red final, no la única.
- **El LLM nunca toca IDs/SQL**: devuelve un índice; los IDs vienen de los candidatos de la DB (scoped). La
  escritura es la tool parametrizada de siempre.
- **PII**: este slice **no** agrega captura de texto libre nuevo a la DB (sigue pendiente la redacción de
  `log_interaction.content` y `update_client.notes` → **slice de Guardrails**, no este). El historial del
  checkpointer puede contener PII conversacional: es el mismo dato que el usuario tipeó, en su propio thread
  aislado; la redacción en entrada/salida del grafo es Guardrails. **Anotado como dependencia de privacidad**:
  con memoria persistente, el caso a favor de Guardrails se fortalece (próximo slice recomendado).
- **Inyección**: el contenido del usuario no se concatena a SQL; `resolve_choice` produce un entero validado. La
  detección de inyección en entrada sigue siendo Guardrails.

## Testing (DoD CLAUDE.md §6)

Patrón establecido: inyección de `gen_llm=`, `monkeypatch` de funciones de módulo, `MemorySaver` para el ciclo
HITL (`tests/test_nodes.py`, `test_hitl_cycle.py`, `test_resolvers.py`, `test_write_tools.py`, `test_*_agent.py`).

- **No-llm** (sin Ollama):
  - `test_state.py` (o donde viva): `new_state` inicializa `pending_clarification=None`.
  - `test_edges.py`: `entry_route` → `"clarify"` con `pending` no vacío, `"router"` sin él / `None`;
    `route_after_clarify is route_after_propose`.
  - `test_choice_agent.py` (nuevo): `resolve_choice` con `gen_llm` fake → índice válido se devuelve; `0` se
    devuelve; índice fuera de rango (> N) → `0`; excepción del LLM → `0`.
  - `test_resolvers.py` (extender): `resolve_single_client` ambiguo → `candidates == clients`; no-ambiguo →
    `candidates == []`. Idem `resolve_single_appointment` ambiguo → `candidates == matches`.
  - `test_clarify_node.py` (nuevo): con `resolve_choice` y `REGISTRY[kind].propose` monkeypatcheados:
    (a) elige 1 + propose→`proposed_action` → estado `{proposed_action set, pending_clarification:None}`;
    (b) **encadena**: propose→`clarification(stage="appointment")` → `pending_clarification.stage=="appointment"`
    y `overrides.client` fijado; (c) no-mapea (`resolve_choice→0`) → `pending_clarification:None` + mensaje de
    reintento, `propose` **no** se llama; (d) índice fuera de rango → no-mapea.
  - `test_nodes.py` (extender): `propose_action_node` con `_handle_proposal_result` → rama clarification setea
    `pending`; rama abstención dura limpia `pending`; rama resuelta setea `proposed_action`. `chitchat_node`
    recibe historial multi-mensaje (verifica que arma el prompt con > 1 mensaje, ventana `N`).
  - `test_*_agent.py` (extender los 5): `propose_X(..., client_override=C)` salta `resolve_single_client` (no lo
    llama) y usa `C`; `cancel`/`reschedule` con `appointment_override=T` saltan `resolve_single_appointment`;
    rama `client_ambiguous` → `ProposalResult.clarification` con `stage="client"` y `candidates`.
    `propose_appointment` **unificado**: usa `resolve_single_client` (no-regresión de mensajes) + expone
    `candidates`; el resolver de profesional intacto.
  - `test_main.py` (extender, con `MemorySaver`): `/chat` a un `thread_id` **nuevo** → input inicial (estado
    arranca limpio); 2º `/chat` al **mismo** `thread_id` → input incremental → el historial **acumula** (>1
    `HumanMessage`) y un `pending_clarification` previo **no** se pisa. Sin `thread_id` → fallback `uuid4`.
  - **No-regresión**: `test_hitl_cycle.py` (5 kinds) verde sin tocar asertos; one-shot de las 5 tools intacto.
- **`-m llm`** (`test_short_term_memory_e2e_llm.py`, nuevo; Ollama + Postgres + `seed_demo.py`):
  - **Cadena cliente→turno**: sembrar dos clientes con nombre que colisione + el elegido con ≥2 turnos futuros;
    *"cancelá el turno de \<nombre ambiguo\>"* → pending stage `client`; *"\<apellido\>"* → pending stage
    `appointment`; *"el del \<día\>"* → `proposed_action` (`kind=="cancel_appointment"`, `appointment_id`
    poblado); `resume="confirm"` → fila `cancelado` en DB (verificada por `id`). Todo el **mismo `thread_id`**.
  - **Slot-filling de turno directo** (cliente único, 2 turnos): *"cancelá el turno de X"* → pending
    `appointment` → *"el primero"* → tarjeta → confirm → cancelado.
  - **Memoria conversacional**: *"mi profesional de referencia es la Dra. Gómez"* → chitchat; *"¿quién dije
    que es mi profesional?"* (mismo thread) → la respuesta menciona "Gómez".
  - **No-mapea**: con un pending abierto, *"mejor mostrame otra cosa"* → `pending_clarification` se limpia +
    mensaje de reintento; el turno siguiente rutea normal.
  - **No-regresión**: one-shot de `reschedule`/`create`/`log`/`update` + abstención dura siguen verdes.
  - Limpieza: el test usa clientes/turnos creados ad-hoc o re-`UPDATE`a para no dejar el seed mutado.
- **Frontend** (`vitest`): `chatStream.test.ts` extendido (el body de `/chat` incluye `thread_id`); `runtime`
  pasa un `threadId` estable. Verde: vitest + lint + build.
- **Gates**: `ruff format` **antes** de `ruff check`; `mypy --config-file backend/pyproject.toml`; `pytest -q`
  (no-llm) verde. **Smoke §2**: la cadena cliente→turno→Confirmar deja la fila `cancelado`; chitchat recuerda
  un dato entre turnos; no-mapea pide reintento; one-shot de las 5 tools + RAG/SQL/chitchat no regresionan.

## Dependencias

Ninguna nueva. `langgraph` ya provee checkpointer + `interrupt`/`Command` (cableado en el `lifespan`);
`with_structured_output` ya se usa; `aget_state` es API estándar de LangGraph. `crypto.randomUUID()` es nativo
del navegador. Sin red saliente fuera de Ollama/Postgres/Qdrant locales (DoD §6.5).

## Definition of Done (CLAUDE.md §6)

1. `ruff`, `mypy --config-file backend/pyproject.toml`, `pytest -q` (no-llm) verdes; `-m llm` verde con Ollama +
   ambos modelos + Postgres + `seed_demo.py`.
2. Tocamos el grafo (entry condicional + nodo `clarify`) y el transporte: smoke §2 pasa, **las escrituras piden
   confirmación de verdad**, el ciclo `interrupt`→`resume` sigue con test no-llm, y las 5 tools one-shot +
   RAG/SQL/chitchat **no regresionan**.
3. No se tocó retrieval/SQL/síntesis ni el **prompt del router** → la suite offline de eval no aplica. Se agrega
   el nuevo `resolve_choice` (mapeo) y `chitchat` contextual; si el e2e mostrara un fallo de mapeo, se agrega un
   caso golden — anotado.
4. Prompts (`resolve_choice`, ajustes de extractores) a mano ahora; recompilar con DSPy = Fase 2.
5. Cero red saliente fuera de Ollama/PG/Qdrant locales.
6. Commit limpio, sin ninguna atribución a Claude (CLAUDE.md §6).

## Riesgos y mitigaciones

- **`resolve_choice` mapea mal** (elige el candidato equivocado): mitigado por prompt conservador (0 si dudoso)
  + guard de rango + el HITL como red final (el usuario confirma el turno concreto en la tarjeta). Caso golden
  si el e2e lo muestra. El riesgo de "confirma apurado el equivocado" es el mismo que el resto del HITL.
- **Re-extracción no-determinística** al re-invocar `propose` con override: `temperature=0` + mismo `question`
  → estable; el override es lo único que cambia. Bajo.
- **`thread_id` estable acumula estado basura** (proposed_action consumido, pending viejo): mitigado por input
  incremental (no pisa) + limpiar `pending_clarification` al resolver/no-mapear + `proposed_action=None` en
  `confirm`. El historial crece pero solo `chitchat` lo lee, con ventana `N`.
- **`aget_state` sin checkpointer** (fallback de tests / lifespan no corrió): devuelve vacío → input inicial
  siempre → sin memoria, idéntico a hoy. No rompe.
- **Encadenado infinito**: imposible — cada ronda fija un slot (cliente→turno→done); el no-mapea corta.
- **Inestabilidad de Ollama bajo carga** (gotcha de e2e): `resolve_choice` fail-closed a 0 → no-mapea (pide
  reintento), nunca escribe; los e2e endurecen asertos (interrupt no-vacuo) y reintentan, sin debilitar.
- **Unificar `propose_appointment`** podría cambiar mensajes de create: se cubre con no-regresión explícita de
  sus ramas de cliente (no-encontrado/ambiguo) en `test_action_agent.py`.
- **PII en el historial persistido**: dato del propio usuario, en su thread aislado; la redacción es el slice de
  Guardrails (este la **refuerza** como próxima prioridad, no la introduce).
- **Aislamiento tenant pre-RLS**: app-level (resolvers scoped + guards en writers); RLS = Fase 4.
