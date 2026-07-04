# Diseño — Context Manager COMPLETO (running_summary + presupuesto de tokens + prefijo estable)

> **Fase 2 · Slice 3** · Fecha: 2026-07-04 · Estado: **aprobado (brainstorming)**, pendiente de `writing-plans`.
> Contrato: CLAUDE.md (local-first, $0, multi-tenant por `practice_id`, escrituras solo por HITL, commits sin atribución a Claude). Diseño de referencia: `Praxia_Blueprint.md` §4.2.
> Continúa el "Context Manager **mínimo**" del Slice 12 (memoria LP): `app/context.py` ya es el punto único de ensamblado; acá se completa.

## 1. Contexto y problema

El Slice 12 dejó `app/context.py` como hogar del ensamblado de contexto, pero solo con `format_memories_block` (inyección de memorias como un 2º system message tras el prompt estable). Faltan las tres piezas que definen el "Context Manager completo" del blueprint §4.2:

- **`running_summary`** — resumen incremental de turnos viejos. Hoy **no existe**: `AgentState` no tiene el campo (`state.py:12-14` lo reserva "para su slice" = ésta); los `messages` crecen sin tope en el checkpointer y **solo `chitchat`** pasa historial al LLM (`_history_messages`, ventana fija `short_term_history_window=10`). Una charla de > 10 mensajes **pierde su cabeza**: el turno 1 desaparece del contexto y nada lo preserva.
- **Presupuesto de tokens** — hoy **no hay conteo ni cap** en ningún lado. Un mensaje gigante (usuario que pega un bloque enorme) puede inflar el prompt; el 12B local degrada con prompts enormes (CLAUDE.md §4/§9) y Ollama con `num_ctx` default (~4096) puede **recortar silenciosamente el head** (incluido el system) si se pasa.
- **Prefijo estable para KV-cache** — el ordenamiento estable→volátil existe de facto (system primero, memorias después) pero no está formalizado ni centralizado; al insertar el summary hay que fijar el orden en un solo lugar.

**Estado del código relevante:**
- `app/context.py` = solo `format_memories_block`.
- `chitchat_node` (`nodes.py:94`) arma inline `[("system", CHITCHAT_SYSTEM), ("system", mem_block)?, *_history_messages(state, 10)]`.
- `sql`/`rag` son **single-turn** (`last_user_text`) + memorias; no usan historial.
- Grafo: `…content leaves… → reflect → END`. `reflect_node` (`memory_nodes.py:39`) es el paso universal de cierre de turno (best-effort, time-boxed).
- `make_llm` (`llm.py`) **no** fija `num_ctx` → Ollama usa el default del modelo.

## 2. Alcance

**En scope (enfocado al camino conversacional):**
- **`app/context.py` como builder único del camino conversacional:** `estimate_tokens`, `format_summary_block`, `build_chat_messages` (ensambla stable→volátil + recorta por presupuesto). Función **pura**.
- **`running_summary`** para el camino multi-turno (chitchat): campo en `AgentState`, **update incremental post-turno** (pliega solo los turnos recién desalojados de la ventana), **best-effort + time-boxed**, con **e4b** (`ollama_model_cheap`), solo cuando hay desalojo.
- **Presupuesto de tokens** por **heurística local** (≈ chars/4), guardrail que recorta el ensamblado de chitchat sin romper el turno.
- **Nodo de cierre de turno generalizado** `consolidate_node` (rename de `reflect_node`): corre memoria-LP (`reflect.run`) y el update del summary **concurrentemente** (`asyncio.gather`), cada uno best-effort + time-boxed.
- `AgentState.running_summary: str` + `AgentState.summarized_count: int`.
- Tests (unit puros + node + 1 e2e-llm de continuidad cross-ventana) + gate `-m "not llm"` verde.

**Fuera de scope (se dejan seams; no construir de más — CLAUDE.md §7):**
- **Continuidad conversacional para `sql`/`rag`** (que vean el summary / el historial) — siguen single-turn. Es el "follow-up contextual sql/rag" diferido desde el Slice 8; **no** se toca acá. `sql`/`rag` siguen usando solo `format_memories_block` (ya KV-ordenado y acotado por top-k).
- **Tokenizer real de Gemma** — es *gated* en HuggingFace (requiere auth para bajarlo → viola $0/sin-red). La heurística queda **swappable** detrás de `estimate_tokens` para cambiarla en Fase 4/vLLM.
- **Fijar `num_ctx` explícito** en `make_llm` — el presupuesto conservador (~3000 < ~4096) ya evita el recorte silencioso; pinnear `num_ctx` queda como fast-follow de hardening.
- **Background/detached reflection y summary** (optimización de latencia) — mismo fast-follow que arrastra reflect desde el Slice 12; acá corren concurrentes en el seam existente (costo marginal ~0), no detached.
- **DSPy** sobre el prompt de sumarización — slice posterior; el eval-gate deja la métrica lista.

## 3. Criterios de éxito (medibles) / DoD

1. **Continuidad cross-ventana:** en un chitchat de > `short_term_history_window` mensajes, un hecho dicho en un turno **ya desalojado** de la ventana sigue influyendo una respuesta posterior (lo cargó el `running_summary`). e2e-llm lo prueba.
2. **Guardrail de presupuesto:** `build_chat_messages` con un presupuesto chico **nunca** dropea `system`/`summary`/turno-actual; dropea historial viejo primero, luego memorias, y como último recurso trunca el turno actual. Determinista, probado unit.
3. **Best-effort:** si la sumarización falla/timeoutea, se **conserva el summary previo** y el turno responde igual; el `done` SSE no se rompe. Probado unit (fake LLM que rompe/timeoutea → state intacto).
4. `pytest -m "not llm"` sigue verde (323 + nuevos); `ruff` + `mypy app/` verdes; `-m eval` no regresiona (casos single-turn → sin desalojo → summary no-op).
5. Smoke: chitchat corto sin summary (no-regresión) + chitchat largo con continuidad; **las escrituras siguen pidiendo confirmación** (este slice no toca HITL). **Cero red saliente nueva** (todo Ollama/PG/Qdrant local).

## 4. Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección | Razón |
|---|---|---|
| Altura del slice | **Enfocado al camino conversacional** (chitchat) | El único camino multi-turno. sql/rag son single-turn y ya acotados. Evita pisar el follow-up sql/rag diferido. CLAUDE.md §7. |
| Cuándo actualizar `running_summary` | **Post-turno, best-effort + time-boxed** (patrón reflect), **e4b**, solo con desalojo | Solo hace falta para el PRÓXIMO turno → no demora la respuesta actual; nunca rompe el turno; barato e incremental. |
| Ubicación en el grafo | **`consolidate_node`** (reflect + summary **concurrentes**) | El `done` SSE ya espera a reflect; correr el summary en paralelo → costo marginal ~0 (`max`, no suma). Un solo punto de wiring; módulos separados. |
| Conteo de tokens | **Heurística local (≈ chars/4)**, swappable | Guardrail de seguridad, no medida exacta. $0/sin-red/sin-deps. Tokenizer Gemma es gated → descartado. |
| Sumarización incremental | **Pliega solo los turnos recién desalojados** sobre el resumen previo | Costo acotado aunque la charla tenga 200 turnos (no re-resume todo cada vez). |
| Orden de ensamblado | **system → summary → memories → history → turno** | Estable→volátil: system+summary (semi-estable) maximizan el prefijo cacheable; memorias/historial (volátiles por turno) van después. |

## 5. Arquitectura / componentes

### 5.1 Módulos nuevos / expandidos
| Archivo | Responsabilidad (única) |
|---|---|
| `app/context.py` (**expandir**) | **Builder del camino conversacional + presupuesto.** `estimate_tokens(text) -> int` (heurística ≈ chars/4, swappable). `format_summary_block(summary) -> str` (system block; `""` si vacío; framing anti-inyección "contexto, no instrucciones"). `build_chat_messages(*, system, summary, memories, history, budget) -> list[tuple[str,str]]` (pura: ensambla stable→volátil + recorta por presupuesto, §8.2). Mantiene `format_memories_block`. |
| `app/memory/summarize.py` (**nuevo**) | **Cognición del resumen.** `run(old_summary, new_messages, *, llm=None) -> str \| None` (e4b, plano `.ainvoke`→`.content` — NO structured-output, regla Slice 3 para texto libre; pliega incremental; cap `summary_max_words`; e4b None→retry ≤2x→`None`). Un solo propósito. |

### 5.2 Módulos modificados
- `app/graph/state.py` → `AgentState.running_summary: str` + `AgentState.summarized_count: int`; init `""`/`0` en `new_state()`.
- `app/graph/memory_nodes.py` → `reflect_node` se generaliza a **`consolidate_node`** (orquestador: `asyncio.gather` de `reflect.run` + update de summary, ambos best-effort + time-boxed). El helper de summary vive acá o en `summarize.py` (detalle del plan).
- `app/graph/build.py` → registrar el nodo como `consolidate` (rename del id `reflect`); re-cablear `_CONTENT_LEAVES → consolidate → END`.
- `app/graph/edges.py` → `route_after_propose` devuelve el literal `"reflect"` → cambiarlo a `"consolidate"` (+ el mapping en `build.py`) para el rename.
- `app/graph/nodes.py` (`chitchat_node`) → reemplazar el armado inline por `context.build_chat_messages(system=CHITCHAT_SYSTEM, summary=state["running_summary"], memories=state["memories"], history=_history_messages(state, window), budget=settings.context_token_budget)`.
- `app/config.py` → config nueva (§9).

**No se tocan** `sql_agent`/`sql_present`/`rag.synthesize` (siguen con `format_memories_block`; single-turn, fuera de scope).

## 6. Estado nuevo (`AgentState`)

- `running_summary: str` — resumen incremental de la conversación previa a la ventana verbatim. `""` si no hubo desalojo. Persiste por el checkpointer (por `thread_id`).
- `summarized_count: int` — cuántos mensajes de `messages` ya están plegados en `running_summary` (puntero de plegado incremental). `0` inicial.

Invariante: `running_summary` cubre `messages[:summarized_count]`; la ventana verbatim cubre `messages[-window:]`. La franja `messages[summarized_count : len-window]` son los **recién desalojados** a plegar en el próximo cierre de turno.

## 7. Flujo del grafo

### 7.1 Topología (antes → después)
**Antes** (Slice 12): `…{rag, chitchat, sql_node, confirm_action} → reflect → END`; `route_after_propose → {confirm_action | reflect}`; `scope_reject → END`.

**Después** (solo cambia el nombre/función del nodo de cierre; los edges son idénticos en forma):
```
{rag, chitchat, sql_node, confirm_action} ─▶ consolidate      ◀ era 'reflect'
propose_action / clarify ─route_after_propose─▶ {confirm_action | consolidate}
scope_reject ─▶ END                                            ◀ igual (no consolida)
consolidate ─▶ END
```
`consolidate_node` corre **concurrentemente** (`asyncio.gather`, cada uno guardado):
- `reflect.run(...)` — memoria de largo plazo (Slice 12, **sin cambios**). Devuelve `{}` (no toca state observable).
- **update de summary** — solo si `summary_enabled` y hay recién-desalojados (§8.1). Devuelve `{"running_summary": …, "summarized_count": …}` o `{}`.

El merge de resultados: reflect no aporta delta de state; el summary sí. Wall-clock ≈ `max(reflect_timeout, summary_timeout)`, no la suma.

### 7.2 Consumo (cada turno de chitchat)
`recall_node` deja `state["memories"]` (Slice 12) → `chitchat_node` llama `build_chat_messages(...)` con el `running_summary` que dejó el turno anterior (vía checkpoint) → streamea. Orden final de mensajes: `[("system", CHITCHAT_SYSTEM), ("system", summary_block)?, ("system", mem_block)?, *history]`, recortado a presupuesto.

## 8. Algoritmos

### 8.1 Update incremental del summary (post-turno, en `consolidate_node`)
```
W = short_term_history_window ; M = len(state["messages"]) ; c = state["summarized_count"]
if not summary_enabled: return {}
evict_upto = M - W                       # todo antes de la ventana verbatim debe estar en el summary
if evict_upto <= c: return {}            # no hay recién-desalojados → no-op (charla corta o sin crecimiento)
newly = state["messages"][c : evict_upto]
new_summary = await summarize.run(state["running_summary"], newly)   # e4b, best-effort + time-boxed
if new_summary is None: return {}        # fallo/timeout → conserva summary y count viejos
return {"running_summary": new_summary, "summarized_count": evict_upto}
```
- **Incremental:** solo pliega `newly` sobre el resumen previo → costo O(turnos-nuevos), no O(historia).
- Corre en **todos** los terminales de contenido (no solo chitchat): el summary refleja la conversación completa (útil cuando el usuario vuelve a chitchat tras sql/acciones). Barato (e4b) y solo con desalojo.

### 8.2 `build_chat_messages` — ensamblado + recorte por presupuesto (función pura)
```
parts = [("system", system)]
if format_summary_block(summary):  parts.append(("system", summary_block))
if format_memories_block(memories): parts.append(("system", mem_block))
parts += history                                    # el ÚLTIMO item es el turno humano actual
# --- recorte por presupuesto (estimate_tokens sobre cada texto + overhead fijo por mensaje) ---
while total_tokens(parts) > budget and hay_historial_viejo_dropeable:
    drop el ítem de historial más viejo (front), preservando el último (turno actual)
if total_tokens(parts) > budget: quitar el bloque de memorias
if total_tokens(parts) > budget: truncar el texto del turno actual con "…[truncado]"
return parts
```
**Inviolables (nunca se dropean):** `system`, `summary_block`, y el **último mensaje humano**. El turno actual puede **truncarse** (no dropearse) como último recurso. `system`+`summary` se asumen acotados por construcción (`summary_max_words`). Determinista, sin efectos, sin fallos.

### 8.3 `estimate_tokens`
`max(1, ceil(len(text) / 4))` por texto (español ≈ 4 chars/token, conservador). Total = Σ por mensaje + `overhead_por_mensaje` fijo (chico). Swappable: toda la aritmética de presupuesto pasa por esta función.

### 8.4 `summarize.run` (e4b, texto libre)
Prompt e4b: *sistema* = "Mantené un resumen conciso y factual (español, 3ª persona, ≤ N palabras) de una conversación; te doy el resumen previo y los turnos nuevos; devolvé el resumen ACTUALIZADO, sin inventar, priorizando hechos/preferencias/decisiones"; *human* = resumen previo (`(vacío)` si `""`) + turnos nuevos formateados. Salida: `.content` stripped, **cap duro** a `summary_max_words`. **NO** structured-output (texto libre → plano, regla Slice 3). e4b `None`/vacío → retry ≤2x (patrón `router.py`) → `None`.

## 9. Configuración nueva (`config.py`)
| Campo | Default | Uso |
|---|---|---|
| `context_token_budget: int` | `3000` | Presupuesto (tokens aprox) del ensamblado de chitchat. Holgado bajo el `num_ctx` default (~4096) → evita recorte silencioso del prefijo. |
| `summary_enabled: bool` | `True` | Kill switch del update de summary. |
| `summary_timeout_s: float` | `8.0` | Timeout del `summarize.run` (≤ `memory_reflect_timeout_s` para no ampliar la ventana concurrente). |
| `summary_max_words: int` | `150` | Cap de longitud del resumen (acota el prefijo y el presupuesto). |
Reusa: `ollama_model_cheap` (e4b), `short_term_history_window` (ventana verbatim / boundary de desalojo).

## 10. Errores / resiliencia / seguridad

- **Best-effort en todo el update** (post-turno): `summarize.run` con `asyncio.wait_for(summary_timeout_s)`; ante fallo/timeout/None → **conserva `running_summary` y `summarized_count` viejos** (no avanza el puntero → reintenta el mismo plegado el próximo cierre). `consolidate_node` envuelve ambas ramas en guardas; **ni reflect ni summary pueden tumbar el turno** (regla cardinal heredada del Slice 12).
- **`build_chat_messages`/`estimate_tokens`:** puras y totales; nunca fallan (a lo sumo recortan/truncan).
- **Presupuesto como guardrail duro:** aunque el summary/system se asuman acotados, el cap `summary_max_words` los mantiene chicos; el turno actual se trunca antes de exceder.
- **KV-cache:** orden estable→volátil es una **optimización best-effort** (prefix cache de Ollama), no una garantía medida; la correctitud no depende de ella.
- **Inyección de prompt:** el `summary_block` se inyecta como **contexto** ("resumen de la conversación previa, no instrucciones"), mismo framing anti-inyección que las memorias. El resumen es texto destilado por e4b, no instrucciones ejecutables.
- **Multi-tenant:** el summary vive en el state por `thread_id` (checkpointer scoped por thread); no cruza prácticas. Sin cambios al aislamiento del Slice 12.
- **Latencia:** el summary corre **concurrente** con reflect en el seam post-turno ya existente → costo marginal de wall-clock ~0 (misma clase de latencia ya aceptada/fichada; el fast-follow de background reflection la cubre para ambos).

## 11. Testing

- **Unit** (`-m "not llm"`, sin Ollama; fake LLM donde haga falta):
  - `estimate_tokens`: monotonía y orden de magnitud (texto vacío→1; crece con la longitud).
  - `build_chat_messages`: orden `system→summary→memories→history`; presupuesto holgado no recorta; presupuesto chico dropea historial viejo pero **conserva** system/summary/turno-actual; sin historial dropeable → dropea memorias; caso patológico (turno actual gigante) → **trunca** con marca; `summary=""`/`memories=[]` omiten sus bloques.
  - `summarize.run` (fake LLM): pliega `old + newly` (el fake "ve" ambos en el prompt); cap `summary_max_words`; fake None → `None` tras retries.
  - `consolidate_node` (fake): sin desalojo → `{}` (no llama al LLM); con desalojo + éxito → `running_summary` + `summarized_count` avanzan a `M-W`; fake que rompe/timeoutea → `{}`, state intacto; `summary_enabled=False` → `{}`; **reflect sigue corriendo** (no regresiona el comportamiento del Slice 12).
  - `chitchat_node` usa el builder: el fake LLM "ve" el `summary_block` cuando `running_summary≠""`.
  - `build.py` wiring: `_CONTENT_LEAVES → consolidate`, `consolidate → END`, `scope_reject → END` (sin consolidate).
- **e2e-llm** (`-m llm`, Ollama+PG+Qdrant reales):
  - Chitchat de > `short_term_history_window` mensajes en un thread; un hecho de un turno **ya desalojado** (p.ej. un nombre/preferencia dicho al principio) sigue influyendo una respuesta posterior → prueba que el `running_summary` lo cargó.
  - No-regresión: chitchat corto (≤ ventana) responde sin summary (`running_summary==""`).
- **Gate:** `-m "not llm"` verde; `-m eval` sin cambios (casos single-turn → `M-W ≤ 0` → summary no-op → chitchat/sql/rag idénticos).

## 12. Seams para slices futuras
- **`num_ctx` explícito** en `make_llm` (hardening; el presupuesto lo vuelve deterministamente seguro).
- **Continuidad conversacional sql/rag** (que consuman summary/historial) — follow-up diferido de Slice 8; `build_chat_messages` ya es el lugar.
- **Tokenizer real** (Gemma/vLLM en Fase 4) detrás de `estimate_tokens`.
- **DSPy** sobre el prompt de `summarize` contra el golden set.
- **Background/detached** para reflect + summary (baja la latencia de cierre de turno).
- **Memoria RICA (update/delete/contradicción)** — el slice siguiente acordado (decisión usuario 2026-07-03).

## 13. Riesgos / gotchas heredados a respetar en implementación
- **Structured-output e4b `None` intermitente** → `summarize` usa texto plano + retry ≤2x (patrón `router.py:37-41`); NO structured-output para texto libre (regla Slice 3).
- **Latencia del cierre de turno** (fast-follow vigente del Slice 12): el summary NO debe ampliarla → corre concurrente y `summary_timeout_s ≤ memory_reflect_timeout_s`.
- **Rename `reflect_node → consolidate_node`** es cross-cutting (build.py + memory_nodes.py + tests que referencian el nodo) → correr la **suite completa** `-m "not llm"`, no solo los archivos tocados (lección Slice 12: la regresión de `_fake_synth` solo la cazó el gate final).
- **`ruff format` antes de `ruff check`**; imports nuevos en tests existentes al TOP (E402).
- **Windows + `dev.py`** (no uvicorn directo, ProactorEventLoop) para el smoke manual; front en `:3100`.
- **mypy:** el gate `mypy app/` está VERDE (el crash histórico no reproduce; mypy pineado `1.13.*`) — no meter literales int ≥ 2^64 en `app/` (orjson en la cache).
