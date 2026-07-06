# Diseño — Memoria RICA (update/delete/contradicción: A auto-supersede + B comando olvidá/corregí)

> **Fase 2 · Slice 4** · Fecha: 2026-07-06 · Estado: **aprobado (brainstorming)**, pendiente de `writing-plans`.
> Contrato: CLAUDE.md (local-first, $0, multi-tenant por `practice_id`, escrituras CRM solo por HITL, commits sin atribución a Claude). Diseño de referencia: `Praxia_Blueprint.md` §4.2.
> Continúa la memoria de largo plazo del Slice 12 (`store`/`recall`/`reflect` scope `practice`) y el Context Manager del Slice 13.

## 1. Contexto y problema

El Slice 12 dejó memoria de largo plazo funcional pero **inmutable**: solo se puede *insertar*. `long_term.store` tiene un único mecanismo defensivo —**dedup**: si el coseno con la vecina más cercana ≥ `memory_dedup_threshold` (0.9), toca `last_used_at` y descarta el candidato (`long_term.py::store`)—. No existe **update** ni **delete** en ninguna capa (ni PG, ni Qdrant, ni reflect, ni un comando de usuario).

Consecuencia (bug destapado en el smoke del Slice 12/13): dos hechos **contradictorios** sobre el mismo sujeto tienen coseno < 0.9 (difieren justo en el valor: "los turnos duran **30** minutos" vs "…duran **45** minutos") → **conviven ambos** en `memories` y el `recall` (top-k por coseno, `memory_min_score=0.5`) **inyecta los dos** en el prompt → el modelo ve una contradicción y responde inconsistente. El usuario no tiene forma de **corregir** ni **olvidar** un dato mal aprendido.

**Estado del código relevante:**
- `app/memory/long_term.py`: `store` (embed → dedup top-1 ≥0.9 → INSERT PG → upsert Qdrant con compensación); `recall` (query top-k, filtro `practice`+`scope='practice'`, devuelve `{id, content, kind, scope}` **sin score**); `touch_last_used`; `ensure_memories_collection`. Content vive en el payload de Qdrant → **el recall NO hace join con PG**.
- `app/memory/reflect.py`: `gate` (e4b sí/no + `is_explicit`) → `extract` (e4b, cap 3) → `store` por candidato; best-effort + `wait_for(memory_reflect_timeout_s=10)`, nunca rompe el turno; `_structured` reintenta ≤2x el None de e4b.
- `app/graph/router.py`: 5 intenciones (`rag`/`sql`/`action`/`chitchat`/`out_of_scope`), e4b + **parseo de texto** (structured del router da None intermitente), fallback seguro `chitchat`.
- `app/graph/edges.py`: `_INTENT_TO_NODE` mapea intención→nodo; `route` cae a `scope_reject` ante intención desconocida.
- `app/graph/build.py`: `router → recall → {rag|sql_node|chitchat|propose_action|scope_reject} → …`; los terminales de **contenido** pasan por `consolidate` (reflect + summary); `scope_reject → END` directo (nada que consolidar).
- Guardrails PII de hoy viven en **ingesta / write-tools** (`guardrails/pii.py`, `db.py`), **no** como nodo de entrada del grafo → **el router es el punto de control de entrada de facto**. (El endurecimiento de guardrails de entrada es un slice posterior de Fase 2.)
- Schema `memories`: `id`, `practice_id`, `scope∈{practice,client,user}` (solo se escribe `practice`), `client_id`/`user_id` (siempre NULL), `kind∈{preferencia,hecho,episodica}`, `content`, `source∈{reflexion,explicito}`, `salience`, `created_at`, `last_used_at`.

## 2. Alcance

**En scope — core "update/delete/contradicción", por dos caminos que comparten primitivas hard-delete:**
- **A (implícito) — auto-supersede en `reflect`:** un hecho nuevo que **contradice/actualiza** a uno viejo lo **supersede** (inserta el nuevo + borra el viejo). Detección por **juez e4b** sobre una **banda de coseno "mismo tema"**.
- **B (explícito) — comando `olvidá/corregí que…`:** nueva intención del router → nodo que **borra/supersede inline con eco** (sin ConfirmCard), con find dirigido y salvaguardas ante ambigüedad/misroute.
- **Primitivas nuevas en `long_term`:** `probe` (clasifica el vecindario en dedup/banda), `forget` (borra PG+Qdrant), `store` extendido (supersede opcional, vector precomputado opcional).
- Tests (unit `-m "not llm"` + node con LLM mockeado + e2e-llm de A y B) + golden del router + gate verde.

**Fuera de scope (se dejan seams; no construir de más — CLAUDE.md §7):**
- **C — recall ponderado por recencia/`salience`.** `salience`/`last_used_at` siguen guardándose y **sin leerse**. El recall sigue siendo coseno puro. (Con A funcionando, las contradictorias dejan de convivir → la ponderación deja de ser urgente.)
- **D — scope `client`/`user` + aflojar el gate anti-PII.** Requiere plumbing de **identidad de usuario** (hoy el request trae solo `practice_id`+`thread_id`; `AgentState` no tiene `user_id`). Roza Fase 4. Las columnas `client_id`/`user_id` siguen NULL.
- **E — inyectar memoria/summary en `propose_action`** (write-tools single-turn). Fast-follow que cruza con D; slice aparte.
- **F — UI de gestión de memorias.** Fase 4.
- **Cambio de schema.** Hard delete ⇒ **cero DDL** (no hacen falta columnas `superseded_at`/`replaces_id`).

## 3. Criterios de éxito (medibles) / DoD

1. **A — supersede por contradicción:** guardada "los turnos duran 30 minutos", un turno posterior donde el usuario dice/afirma "ahora duran 45" ⇒ tras el cierre de turno, `recall` **ya no trae la de 30** y **sí trae la de 45**. e2e-llm.
2. **B — olvido:** "olvidá que los turnos duran 45 minutos" ⇒ `recall` **ya no la trae**; el asistente **ecoa** qué olvidó. e2e-llm + unit (mock).
3. **B — corrección:** "corregí: los turnos ahora duran 60 minutos" ⇒ la vieja se supersede por la nueva; eco. unit (mock) + e2e-llm.
4. **Salvaguardas de B (deterministas, unit):** comando **sin match** ⇒ "no tengo nada guardado sobre eso", **no borra**. Comando **ambiguo** (varias fuertes, ninguna dominante) ⇒ pide detalle, **no borra**. **Misroute** (el router manda algo que no es comando) ⇒ el nodo se **auto-verifica** (`operation=none`) y **cae a `chitchat`**, **no borra**.
5. **No-regresión:** el dedup actual sigue (≥0.9 → skip); `reflect` sin contradicción inserta normal; `store` sin `vector`/`supersede_ids` se comporta **idéntico** al de hoy (callers/tests intactos). Gate `-m "not llm"` verde (343 + nuevos); `ruff` + `mypy app/` verdes; `-m eval` sin regresión; **las escrituras CRM siguen abriendo ConfirmCard** (HITL intacto). **Cero red saliente nueva** (todo Ollama/PG/Qdrant local).
6. **Consistencia PG↔Qdrant:** ni supersede ni forget dejan una **memoria fantasma** (punto en Qdrant sin fila en PG → recall la vería). Probado unit contra los stores reales.

## 4. Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección | Razón |
|---|---|---|
| Altura del slice | **A+B core** (contradicción + olvido) | Es el headline literal "update/delete/contradicción". A y B comparten la primitiva hard-delete. C/D/E/F afuera. |
| Semántica de borrado | **Hard delete** (`supersede`=borrar viejo+insertar nuevo; `forget`=borrar) | YAGNI: cero DDL, recall intacto. La memoria es **regenerable** (reflect la reaprende). `interactions`/`agent_runs` ya auditan a nivel acción; la memoria no es dato CRM/PII. |
| Comando de usuario | **Inline con eco**, sin ConfirmCard | La memoria es **estado del asistente**, no dato CRM; la HITL de CLAUDE.md §4 es para writes CRM con tools parametrizadas. El usuario ya pide el borrado explícitamente → confirmar sería fricción redundante. Cero frontend nuevo. |
| Routing de B | **6ta intención `memory_command`** en el router + **node self-verify → chitchat** | El router es el punto de control de entrada (CLAUDE.md §4: no esquivarlo). El fallback del nodo neutraliza la fragilidad del router e4b: una mala clasificación **nunca borra**. |
| Detección de A | **Juez e4b** sobre **banda `[memory_contradiction_low=0.6, memory_dedup_threshold=0.9)`**, **sesgo a False** | Precisión > recall: un supersede borra una memoria buena si el juez se equivoca. La banda evita comparar el candidato contra todo el corpus (arriba de 0.9 ya es dedup; abajo de 0.6 es otro tema). |
| Cierre de turno de B | **`memory_command → END`** (salta `consolidate`) | Evita el **loop de re-aprendizaje**: si pasara por `consolidate`, `reflect` re-guardaría en el mismo turno justo lo que se acaba de olvidar. El `running_summary` hace catch-up el turno siguiente (invariante de fold del Slice 13). |

## 5. Arquitectura / componentes

### 5.1 Módulos nuevos / expandidos
| Archivo | Responsabilidad (única) |
|---|---|
| `app/memory/long_term.py` (**expandir**) | Capa de vectores/PG. **Nuevo** `probe(practice_id, content) -> Probe{vector, dedup_id, related[Neighbor]}` (1 embed + 1 query; clasifica el vecindario). **Nuevo** `forget(practice_id, ids) -> int` (borra Qdrant→PG). `store` **extendido** con `vector`/`supersede_ids` opcionales (§8.3). `recall` **+ campo `score`** en cada dict (backward-compatible: los callers actuales lo ignoran). Sin dependencia de LLM (esa cognición vive en `reflect`/el nodo). |
| `app/memory/reflect.py` (**expandir**) | **Nuevo** juez de contradicción `contradiction_judge(new_content, existing_content) -> bool` (e4b structured, `_structured` con retry, sesgo a False). Flujo por candidato pasa a `probe → (dedup? / judge banda) → store(...)` (§8.2). |
| `app/graph/memory_command.py` (**nuevo**) | Nodo `memory_command_node(state) -> dict` (B): extrae `{operation, target, new_value}` (e4b structured), find dirigido, `forget`/`store(supersede)`, eco; **self-verify → `chitchat`** ante `none`/fallo. Un solo propósito. *(Alternativa: alojarlo en `memory_nodes.py`; se decide en el plan.)* |

### 5.2 Módulos modificados
- `app/graph/router.py` → `INTENTS += ("memory_command",)`; línea nueva en `ROUTER_PROMPT` con ejemplos (`"olvidá que…"`, `"ya no…"`, `"corregí que…"`, `"borrá de tu memoria…"`). El parseo/fallback existente no cambia.
- `app/graph/edges.py` → `_INTENT_TO_NODE["memory_command"] = "memory_command"`.
- `app/graph/build.py` → registrar nodo `memory_command`; agregarlo al dict de `add_conditional_edges("recall", route, {...})`; `g.add_edge("memory_command", END)` (**no** pasa por `consolidate`).
- `app/config.py` → config nueva (§9).

**No se tocan** `sql_agent`/`sql_present`/`rag`/`chitchat`/`context.py`/`summarize.py`/`consolidate_node` (A vive en reflect; B es un camino nuevo que termina en END).

## 6. Estado (`AgentState`) — **sin campos nuevos**

B se resuelve **dentro del nodo** (extracción + find + write + eco en un turno); no necesita persistir nada en el state. La intención `memory_command` viaja en el campo `intent` existente (un string más). No hay cambios a `state.py`/`new_state`.

## 7. Flujo del grafo

### 7.1 Topología (antes → después)
**Antes:** `router → recall → {rag|sql_node|chitchat|propose_action|scope_reject}`; contenido `→ consolidate → END`; `scope_reject → END`.

**Después** (una rama nueva; el resto idéntico):
```
router → recall ─route─▶ {rag | sql_node | chitchat | propose_action | scope_reject | memory_command}   ◀ +1
memory_command ─▶ END        ◀ NUEVO: salta consolidate (no reflect → no re-aprende lo olvidado)
scope_reject   ─▶ END        ◀ igual
{rag,chitchat,sql_node,confirm_action} ─▶ consolidate ─▶ END   ◀ igual
```

### 7.2 Camino A (auto-supersede) — dentro de `consolidate → reflect.run`
`consolidate_node` (Slice 13) corre `reflect.run` + summary concurrentes. **A modifica solo la lógica interna de `reflect`** (cómo se persiste cada candidato); la topología no cambia. Best-effort + time-boxed heredado: si el juez/probe falla o timeoutea, el candidato cae al `store` normal o se saltea; **nunca rompe el turno**.

### 7.3 Camino B (comando) — nodo dedicado
`recall_node` corre igual (deja `state["memories"]` del texto crudo; el nodo lo ignora y hace su **propio** find dirigido por `target`). `memory_command_node` extrae la operación, resuelve, ecoa, y va a `END`.

## 8. Algoritmos

### 8.1 `probe` (long_term, capa vectorial pura)
```
async def probe(practice_id, content) -> Probe:
    vector = await embed_query(content)
    points = query_points(memories, query=vector, filter=practice+scope,
                          limit=memory_top_k, with_payload=True)   # ordenados por score desc
    dedup_id = points[0].id  if points and points[0].score >= memory_dedup_threshold  else None
    related  = [Neighbor(id, payload["content"], score) for p in points
                if memory_contradiction_low <= p.score < memory_dedup_threshold]
    return Probe(vector, dedup_id, related[:memory_contradiction_max_candidates])
```
Una embed + una query. `dedup_id` reproduce el dedup actual; `related` es la banda a juzgar.

### 8.2 Persistencia de un candidato en `reflect` (A)
```
async def _store_candidate(practice_id, cand, source, salience):
    if not memory_contradiction_enabled:
        await long_term.store(practice_id, kind=cand.kind, content=cand.content,
                              source=source, salience=salience)          # legacy: embed + dedup
        return
    p = await long_term.probe(practice_id, cand.content)
    if p.dedup_id:                                                       # casi-idéntica → dedup actual
        await long_term.touch_last_used([p.dedup_id]); return
    supersede_ids = [n.id for n in p.related
                     if await contradiction_judge(cand.content, n.content)]
    await long_term.store(practice_id, kind=cand.kind, content=cand.content,
                          source=source, salience=salience,
                          vector=p.vector, supersede_ids=supersede_ids)  # reusa el vector; borra viejas
```
`contradiction_judge`: e4b structured (`SupersedeVerdict{supersedes: bool, reason: str}`), `_structured` con retry; prompt estricto, sesgo a False (§10). Si el juez devuelve None (e4b caído) → se trata como False (no supersede) → inserta normal (fail-safe: nunca borra por incertidumbre).

### 8.3 `store` extendido (backward-compatible)
```
async def store(practice_id, *, kind, content, source, salience,
                vector=None, supersede_ids=()) -> str | None:
    if vector is None:                                # camino legacy (callers/tests actuales)
        vector = await embed_query(content)
        m = await _top_match(practice_id, vector)
        if m and m[1] >= memory_dedup_threshold:
            await touch_last_used([m[0]]); return None # dedup idéntico al de hoy
    mem_id = uuid4()
    INSERT INTO memories (...)                        # PG primero
    try: qdrant.upsert(mem_id, vector, payload)       # luego Qdrant
    except: DELETE mem_id from PG; raise              # compensación (igual que hoy)
    if supersede_ids:                                 # el nuevo YA es durable → recién ahora borro viejas
        try: await forget(practice_id, list(supersede_ids))
        except: log.warning("supersede: forget de viejas falló (orphan, no fatal)")
    return mem_id
```
**Orden seguro:** el nuevo queda durable **antes** de borrar los viejos. Un fallo intermedio deja —en el peor caso— viejo+nuevo conviviendo (estado de hoy, autosana en la próxima contradicción); **nunca** pierde dato. `vector` provisto ⇒ el caller ya hizo `probe` ⇒ **no** se re-deduplica (evita doble query).

### 8.4 `forget` (long_term)
```
async def forget(practice_id, ids) -> int:
    if not ids: return 0
    qdrant.delete(memories, points=ids)                                  # Qdrant PRIMERO
    n = execute("DELETE FROM memories WHERE id = ANY($1) AND practice_id = $2", ids, practice_id)
    return n
```
**Orden inverso al de `store` y por la misma razón:** el recall lee el `content` del **payload de Qdrant** (sin join a PG). Borrar Qdrant primero garantiza que —ante un fallo entre las dos— nunca quede un **punto huérfano** que el recall mostraría como memoria fantasma. Un huérfano al revés (fila PG sin vector) es invisible al recall → tolerable. El `AND practice_id` es defensa en profundidad (los ids ya vienen de un find practice-scoped).

### 8.5 `memory_command_node` (B)
```
cmd = await extract_command(last_user_text(state))     # e4b structured + retry → MemoryCommand | None
if cmd is None or cmd.operation == "none":
    return await chitchat_node(state)                  # self-verify: misroute → chat normal, NO borra
matches = [m for m in await long_term.recall(cmd.target, practice_id)   # recall ahora trae score
           if m["score"] >= memory_forget_min_score]
top = matches[0] if matches else None
confident = top and (top["score"] >= memory_dedup_threshold or len(matches) == 1)
if not matches:
    msg = "No tengo nada guardado sobre eso."
elif not confident:                                     # varias fuertes, ninguna casi-exacta → no adivina
    msg = "Encontré varias cosas parecidas; decime con más detalle cuál querés que olvide/corrija."
elif cmd.operation == "forget":
    await long_term.forget(practice_id, [top["id"]])
    msg = f"Listo, me olvidé de: “{top['content']}”."
else:  # correct
    await long_term.store(practice_id, kind=top["kind"], content=cmd.new_value,
                          source="explicito", salience=0.8, supersede_ids=[top["id"]])
    msg = f"Corregido. Ahora recuerdo: “{cmd.new_value}”."
write_token(msg); write_sources([]); return {"sources": [], "messages": [AIMessage(content=msg)]}
```
`MemoryCommand{operation: Literal["forget","correct","none"], target: str, new_value: str}`. **Regla de confianza para un op destructivo:** actúa solo si el top es casi-exacto (≥0.9) **o** es el único match fuerte (≥`memory_forget_min_score`); si hay varias fuertes sin una dominante, **pide detalle** (no borra). `correct` sin `new_value` útil → tratar como `forget` del target o pedir detalle (detalle del plan).

## 9. Configuración nueva (`config.py`)
| Campo | Default | Uso |
|---|---|---|
| `memory_contradiction_enabled: bool` | `True` | Kill switch de A. `False` ⇒ `store` legacy (solo dedup). |
| `memory_contradiction_low: float` | `0.6` | Piso de la banda "mismo tema" a juzgar (techo = `memory_dedup_threshold`). |
| `memory_contradiction_max_candidates: int` | `3` | Cap de vecinos juzgados por candidato (acota llamadas e4b). |
| `memory_command_enabled: bool` | `True` | Kill switch de B (con `False`, el router no debería enrutar acá; el nodo igual se defiende cayendo a chitchat). |
| `memory_forget_min_score: float` | `0.6` | Umbral de confianza del find de B para actuar sobre un match. |
Reusa: `memory_dedup_threshold` (0.9, techo de banda A + bar "casi-exacto" de B), `memory_top_k` (límite de `probe`/`recall`), `ollama_model_cheap` (e4b para juez + extracción de comando).

## 10. Errores / resiliencia / seguridad

- **A es best-effort (heredado):** vive dentro de `reflect.run` (gate/extract/store con `wait_for(memory_reflect_timeout_s)`); `probe`/juez/`store` que fallen o timeouteen **no rompen el turno**. Juez None (e4b caído) ⇒ **no** supersede (fail-safe: nunca borra por incertidumbre).
- **B nunca borra por error:** (a) self-verify `operation=none` → chitchat; (b) sin match → mensaje, no borra; (c) varias fuertes sin dominante → pide detalle, no borra; (d) `extract_command` None → chitchat.
- **Consistencia PG↔Qdrant:** `store` inserta el nuevo antes de borrar viejos; `forget` borra Qdrant antes que PG. Ambos órdenes eligen, ante fallo parcial, el estado que **no** genera memoria fantasma (punto Qdrant sin PG). Peor caso = orphan tolerable, autosana.
- **Loop de re-aprendizaje:** `memory_command → END` (salta `reflect`). El `running_summary` puede reflejar "el usuario pidió olvidar X" (es contexto, no una memoria; reflect no lee el summary) → no reintroduce el hecho.
- **Inyección de prompt:** el juez de contradicción y el extractor de comando reciben **contenido del usuario + memorias existentes** como datos a clasificar, no como instrucciones. Un doc/mensaje no puede forzar un borrado arbitrario: B exige un match semántico real (find scoped por `practice_id`) y confianza mínima; A solo supersede dentro de la banda + veredicto estricto.
- **Multi-tenant:** `probe`/`forget`/`recall` filtran por `practice_id` (+`scope='practice'`); `forget` reafirma `AND practice_id` en el DELETE. Ninguna práctica puede borrar/superseder memorias de otra.
- **Regenerabilidad:** hard delete es aceptable porque reflect reaprende un hecho aún vigente si el usuario lo vuelve a mencionar; un olvido erróneo se revierte re-diciendo el dato.

## 11. Testing

- **Unit** (`-m "not llm"`, stores reales PG+Qdrant; sin Ollama, LLM mockeado donde haga falta):
  - `long_term.probe`: clasifica dedup (≥0.9) vs banda (`[0.6,0.9)`) vs descarte (<0.6); respeta `max_candidates`; devuelve el vector.
  - `long_term.store(supersede_ids=…)`: inserta el nuevo **y** borra los viejos (PG y Qdrant); orden seguro (si el upsert del nuevo se fuerza a fallar → los viejos **siguen**); sin `vector` ⇒ dedup legacy idéntico.
  - `long_term.forget`: borra PG+Qdrant; `recall` ya no la trae; scoped por `practice_id` (no borra de otra práctica); `[]` → 0.
  - `recall`: ahora incluye `score`; callers existentes (que leen `id`/`content`) intactos.
  - `reflect._store_candidate` (juez mockeado): `supersedes=True` → `store` con `supersede_ids`; `False` → insert normal; `dedup_id` → solo `touch`; juez None → no supersede; `contradiction_enabled=False` → store legacy.
  - `memory_command_node` (LLM + long_term mockeados): forget con 1 match → `forget` + eco; sin match → mensaje sin borrar; varias fuertes → pide detalle sin borrar; `operation=none`/extract None → cae a `chitchat` (verifica que NO llama `forget`); correct → `store(supersede_ids)` + eco.
  - `build.py` wiring: `intent=memory_command → memory_command → END` (NO pasa por `consolidate`); las demás ramas intactas.
  - `router.classify_intent` (fake LLM): "olvidá que…"/"corregí que…" → `memory_command`; el fallback y las 5 intenciones previas no regresionan.
- **e2e-llm** (`-m llm`, Ollama+PG+Qdrant reales, `checkpointer=None`):
  - **A:** thread donde se afirma "los turnos duran 30 min", luego "ahora duran 45" → tras el turno, `recall("duración de los turnos")` trae **solo** la de 45.
  - **B forget:** memoria sembrada → "olvidá que …" → `recall` no la trae; el eco lo confirma.
  - **B correct:** "corregí: … ahora …" → la vieja se fue, la nueva está.
- **Gate:** `-m "not llm"` verde (343 + nuevos); `-m eval` sin regresión (los golden actuales no disparan contradicción/comando → comportamiento idéntico); `ruff`/`mypy app/` verdes.
- **Smoke navegador:** (1) sembrar "los turnos duran 30", decir "ahora 45", preguntar la duración → responde 45 (no ambas); (2) "olvidá que …" → confirma y deja de saberlo; (3) una escritura CRM ("agendá un turno…") **sigue abriendo la ConfirmCard** (HITL intacto).

## 12. Seams para slices futuras
- **C — recall ponderado** por recencia (`last_used_at`)/`salience` (ya persistidos, sin leer).
- **D — scope `client`/`user` + aflojar gate anti-PII** (necesita identidad de usuario en el request/state; roza Fase 4).
- **E — memoria/summary en `propose_action`** (write-tools single-turn; fast-follow subido de prioridad, cruza con D).
- **Desambiguación conversacional de B** reusando el mecanismo `pending_clarification`/`clarify_node` (hoy: pedir reformular). Fast-follow.
- **Caso de memoria en el eval-gate** ("Task 11" diferida del Slice 11): golden que valide A/B como métrica.
- **DSPy** sobre el prompt del juez de contradicción / del router (al agregar la 6ta intención) contra el golden set.
- **Micro-opt:** `recall_node` corre en el turno de comando y su salida se ignora (el nodo hace su propio find); saltarlo si `intent=memory_command`.

## 13. Riesgos / gotchas heredados a respetar en implementación
- **Structured-output e4b `None` intermitente** → `contradiction_judge` y `extract_command` usan `_structured` con retry ≤2x (patrón `reflect.py`); ante None persistente → fail-safe (no supersede / cae a chitchat).
- **6ta intención en un router e4b frágil:** mitigado por el self-verify del nodo (misroute → chitchat, nunca borra). Agregar golden cases; **no** editar el prompt del router a ciegas (revisar que no degrade las 5 previas).
- **Firma de `store` (cross-cutting):** nuevos params son opcionales (backward-compatible), pero igual **correr la suite completa `-m "not llm"`**, no solo los archivos tocados (lección Slice 12: la regresión de `_fake_synth` solo la cazó el gate final).
- **`recall` ahora devuelve `score`:** verificar que `recall_node` y cualquier consumidor no rompan (solo agregan un campo).
- **`ruff format` antes de `ruff check`**; imports nuevos en tests existentes al TOP (E402).
- **`test_vectorstore` wipea el Qdrant compartido bajo `-m "not llm"`** → re-sembrar antes del smoke con RAG; los tests de memoria crean/limpian sus propias memorias.
- **Windows + `dev.py`** (no uvicorn directo, ProactorEventLoop) para el smoke; front en `:3100`; si el chat da 500, chequear que `dev.py` esté arriba en `:8000`.
- **mypy `app/` VERDE** (pineado `1.13.*`) — no meter literales int ≥ 2^64 en `app/`.
