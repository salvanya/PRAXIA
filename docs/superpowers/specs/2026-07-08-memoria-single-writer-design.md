# Diseño — Calidad de escritura de memoria (reflect = único escritor + reflect user-only)

> **Fase 2 · Fast-follow #1b (misma rama `fase2/rag-memoria`, sobre el RAG memory-aware)** · Fecha: 2026-07-08 · Estado: **aprobado (brainstorming)**, pendiente de `writing-plans`.
> Contrato: CLAUDE.md (local-first, $0, multi-tenant por `practice_id`, escrituras CRM solo por HITL, commits sin atribución a Claude).
> Complementa el spec `2026-07-07-rag-memoria-merge-design.md`: aquel hizo que RAG **use** la memoria; éste hace que la memoria se **escriba bien** desde lenguaje natural, para que el merge/precedencia sea usable end-to-end.

## 1. Contexto y problema (destapado por el smoke, evidenciado)

El smoke navegador del RAG memory-aware validó memory-only (test 1) y el merge con memoria bien formada (grafo real), pero el **merge por lenguaje natural** ("le digo a Praxia que ahora dura 90 y que pise el protocolo") **falló**. Diagnóstico (systematic-debugging, evidencia en DB + `recall` probe):

1. **A — el hecho se guarda angosto y no recupera.** *"en realidad ahora la primera consulta dura 90 minutos"* → el router e4b lo **misrutea** a la intención `memoria` → `memory_command` (`extract_command` e4b también misfire) lo trata como `operation=correct` y guarda `new_value="90 minutos"` **sin sujeto** (fix del turno-7). Ese "90 minutos" embebe lejos de *"¿cuánto dura la primera consulta?"* → **cae bajo el piso `memory_min_score=0.5` → nunca lo trae `recall`** (probado: recall del "90 minutos" = 0.636 aun con la query conteniéndolo; para la pregunta real, 0 recuperado).
2. **B — `reflect` contamina con respuestas del doc.** `reflect` corre en turnos RAG y **extrae el answer del asistente** (*"La primera consulta dura 60 minutos [1]"*) como memoria → recall 0.85 → **refuerza el doc**, y aunque el "90" recuperara, un "60" bien formado le gana.

**Clave:** los prompts del router (`router.py`) y del extractor (`memory_command.py`) **YA listan** *"en realidad ahora duran 45"* como chitchat/`none`. El problema **no es el prompt**: el e4b los ignora de forma intermitente — es fiabilidad del modelo chico, tarea de **DSPy** (próximo slice). Hay **dos escritores de memoria**: `reflect` (buen extractor: hechos autocontenidos + auto-supersede de Slice 14) y `memory_command` (mal extractor: `new_value` angosto).

**Estado del código relevante:**
- `app/graph/memory_command.py::memory_command_node`: `cmd is None|none` → chitchat; luego `recall(cmd.target)` → match confiable → `forget`/`correct`(supersede); `top is None`+correct → **store del `new_value`** (turno-7) + eco *"ahora lo recuerdo: «…»"*; forget-sin-match → *"No tengo nada guardado"*; ambiguo → pide detalle. `skip_reflect = (operation=="forget")`.
- `app/memory/reflect.py`: `gate(user, assistant)` + `extract(user, assistant)` usan `_turn(user, assistant)` (**ambos textos**); `run(practice, user, assistant)` best-effort + `wait_for(10s)`. `_store_candidate` (probe → judge_neighbor → supersede/dedup/insert) = el auto-supersede de Slice 14.
- `app/graph/memory_nodes.py::_reflect_delta`: `reflect.run(practice, last_user_text, _last_ai_text(messages))`.
- `app/graph/edges.py::route_after_memory_command`: `skip_reflect` → END, si no → consolidate. `build.py`: `memory_command` con edge condicional; `_CONTENT_LEAVES` (rag/chitchat/sql/confirm) → consolidate.

## 2. Alcance

**En scope (defensa en profundidad; NO se toca el router ni se hace DSPy):**
- **A — `reflect` es el único escritor de hechos.** `memory_command` **solo BORRA** (`forget`). TODO `correct` (con o sin match) → **chitchat + reflect** (que extrae el hecho autocontenido del turno del usuario y auto-supersede). Se elimina el store del `new_value` angosto (revierte el turno-7).
- **B — `reflect` lee solo el turno del USUARIO.** `gate`/`extract`/`run` reciben solo `user_text` (se dropea `assistant_text`); prompts ajustados. reflect captura lo que el usuario **afirma/enseña**, nunca lo que el asistente **recuperó** de un doc/DB.
- Tests (unit `-m "not llm"` + e2e-llm del escenario del smoke) + gate verde + eval sin regresión.

**Fuera de scope (no construir de más — CLAUDE.md §7):**
- **Router / clasificación e4b.** Los prompts ya son correctos; la fiabilidad es de **DSPy** (próximo slice). Este diseño hace el misruteo **inofensivo**, no lo elimina.
- **Config nueva / DDL / frontend.** Ninguno.
- **`extract_command` prompt.** Se conserva (el nodo solo pasa a chequear `operation != "forget"`); simplificarlo a "¿es un forget?" es un fast-follow.
- Recall ponderado por recencia/salience (diferido C de Memoria RICA), reflect-background/latencia (fast-follow #2, sigue abierto).

## 3. Criterios de éxito (medibles) / DoD

1. **Merge por lenguaje natural (e2e-llm, el escenario del smoke):** turno 1 *"en realidad ahora la primera consulta dura 90 minutos"* → turno 2 *"¿cuánto dura la primera consulta?"* ⇒ la respuesta **contiene 90** (lidera con la memoria) y menciona/cita el protocolo (60). Con memoria de práctica limpia al inicio.
2. **`reflect` no contamina (unit/e2e):** un turno RAG (*"¿cuánto dura la primera consulta?"*) **no** crea una memoria del answer del doc (la `gate` sobre la pregunta del usuario → `worth_remembering=false`).
3. **`memory_command` solo borra:** `correct` (cualquiera) ⇒ `chitchat_node` + `skip_reflect=False`, **NO** llama `long_term.store`. `forget` con match confiable ⇒ borra (intacto). unit determinista.
4. **No-regresión:** `forget` (match/sin-match/ambiguo) intacto; el auto-supersede de Slice 14 sigue (ahora es el que resuelve las correcciones, vía reflect). Gate `-m "not llm"` verde; `ruff`+`mypy app/` verdes; `-m eval` **sin regresión** (incl. el caso `memory_answer`, ahora que reflect no guarda el answer del RAG). HITL de escrituras CRM intacto. Cero red saliente nueva.
5. **Multi-tenant:** `reflect`/`memory_command` siguen scoped por `practice_id`.

## 4. Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección | Razón |
|---|---|---|
| Quién escribe hechos | **`reflect` único escritor**; `memory_command` solo `forget` | El e4b misrutea afirmaciones a `memoria`; el store de `memory_command` es angosto (`new_value`) y no recupera. reflect extrae hechos autocontenidos + auto-supersede (Slice 14) → resuelve match Y sin-match con contexto completo. |
| `correct` con match | **También defiere a reflect** (no supersede en `memory_command`) | El supersede-con-match **también** sufre el `new_value` angosto (una afirmación misruteada con match supersede-aría con "90 minutos"). reflect lo hace bien en ambos casos. Lo más fiel a "único escritor". (Trade-off: se pierde el eco *"Corregido…"*; se da un ack de chitchat.) |
| Fuente de reflect | **Solo `user_text`** (gate+extract) | La memoria guarda lo que el **usuario** enseña, no lo que el asistente **recupera** de docs/DB. Simple, sin plumbing de intent, robusto ante misruteos. Un turno RAG (pregunta del usuario) → gate false → no guarda. |
| Router | **Sin cambios** | Los prompts ya son correctos; la fiabilidad e4b es de DSPy. A+B hacen el misruteo inofensivo (defensa en profundidad, CLAUDE.md §5: no esquivar el router, sí endurecer aguas abajo). |
| Turno-7 (`corregí que X sin match`) | **Cubierto por reflect** | reflect lee `user_text="corregí que X"` → extrae X (autocontenido) → guarda. Best-effort (mismo de siempre); ack de chitchat en vez del eco confirmatorio. |

## 5. Arquitectura / componentes (superficie chica)

| Archivo | Cambio |
|---|---|
| `app/graph/memory_command.py` | `memory_command_node`: si `cmd is None or cmd.operation != "forget"` → `chitchat_node` + `skip_reflect=False`. Solo `forget` sigue al `recall`/match/`forget`. Se elimina la rama de store del `new_value` (turno-7) y el `correct`-con-match. `skip_reflect=True` para todo `forget`. |
| `app/memory/reflect.py` | `gate(user_text)` / `extract(user_text)` / `_reflect(practice, user_text)` / `run(practice, user_text)` — solo `user_text`. Prompts `GATE_PROMPT`/`EXTRACT_PROMPT` → "dado lo que dijo el usuario". Se elimina `_turn` (o queda sin uso → remover). Guard `if not user_text`. |
| `app/graph/memory_nodes.py` | `_reflect_delta`: `reflect.run(practice, last_user_text(state))` (dropea `_last_ai_text`). `_last_ai_text` queda sin uso → remover. |

**No se tocan:** `router.py`, `edges.py`, `build.py` (la topología `memory_command`→condicional sigue igual; `skip_reflect` viaja igual), `long_term.py`, RAG, config, frontend.

## 6. Algoritmos

### 6.1 `memory_command_node` (A) — solo forget escribe
```
cmd = await extract_command(text) if memory_command_enabled else None
if cmd is None or cmd.operation != "forget":
    return {**await chitchat_node(state), "skip_reflect": False}   # correct/none/misroute → reflect
# forget: único camino que borra
matches = [m for m in await recall(cmd.target, practice_id) if m["score"] >= memory_forget_min_score]
top = matches[0] if matches else None
confident = top is not None and (top["score"] >= memory_dedup_threshold or len(matches) == 1)
if top is None:        msg = "No tengo nada guardado sobre eso."
elif not confident:    msg = "Encontré varias cosas parecidas; decime con más detalle cuál querés que olvide."
else:                  await forget(practice_id, [top["id"]]); msg = f"Listo, me olvidé de: «{top['content']}»."
write_token(msg); write_sources([])
return {"sources": [], "messages": [AIMessage(content=msg)], "skip_reflect": True}
```
Salvaguardas de B intactas: misroute → chitchat (nunca borra); forget solo con match confiable; ambiguo → pide detalle. `correct` ya no borra ni escribe en el nodo.

### 6.2 `reflect` (B) — solo user_text
```
async def gate(user_text) -> GateVerdict|None:      _structured(GateVerdict,  [(sys, GATE_PROMPT),    (human, user_text)])
async def extract(user_text) -> list[Candidate]:    _structured(ExtractedMemories, [(sys, EXTRACT_PROMPT), (human, user_text)])
async def _reflect(practice, user_text):
    v = await gate(user_text)
    if v is None or not v.worth_remembering: return
    source, salience = ("explicito",0.8) if v.is_explicit else ("reflexion",0.5)
    for c in await extract(user_text): await _store_candidate(practice, c, source, salience)
async def run(practice, user_text):
    if not memory_reflect_enabled or not user_text: return
    try: await wait_for(_reflect(practice, user_text), timeout=memory_reflect_timeout_s)
    except: log.warning("reflect best-effort falló")
```
`_store_candidate` (probe → judge_neighbor → supersede/dedup/insert) **no cambia**: es el que ahora resuelve las correcciones (extrae "la primera consulta dura 90 minutos", supersede el "60" si existe).

## 7. Testing
- **Unit (`-m "not llm"`; LLM mockeado):**
  - `memory_command` (mock `extract_command`, `chitchat_node`, `long_term`): `operation=correct` (con y sin match) ⇒ devuelve chitchat + `skip_reflect=False`, **no** llama `store`; `operation=none`/`cmd None` ⇒ chitchat; `forget` con 1 match ⇒ `forget` + eco, `skip_reflect=True`; forget sin match ⇒ mensaje sin borrar; forget ambiguo ⇒ pide detalle sin borrar.
  - `reflect` (mock LLM capturando mensajes): `gate`/`extract` reciben `("human", user_text)` **sin** texto del asistente; `run` sobre una PREGUNTA ⇒ gate `worth_remembering=false` ⇒ no `store`; `run` sobre una afirmación ⇒ extrae + store.
  - Firmas: `reflect.run`/`gate`/`extract` toman solo `user_text`; `_reflect_delta` no pasa assistant.
- **e2e-llm (`-m llm`, Ollama+PG+Qdrant, `checkpointer=None`):**
  - **Merge por lenguaje natural (DECISIVO):** práctica limpia → grafo T1 *"en realidad ahora la primera consulta dura 90 minutos"* → T2 *"¿cuánto dura la primera consulta?"* ⇒ answer contiene **"90"** (aserción del mecanismo; wording relajado). Cierra el gap del smoke.
  - **reflect no contamina:** grafo con *"¿cuánto dura la primera consulta?"* (RAG) ⇒ tras el turno, **no** hay memoria nueva "…60 minutos" (la pregunta no es worth_remembering).
  - **B-correct existente** (`test_B_correct_command_supersedes_via_graph`): *"corregí: los turnos ahora duran 45"* ⇒ sigue quedando 45 y no 30 — **ahora vía reflect** (no memory_command). Verificar que pasa (puede requerir endurecer la aserción al mecanismo / tolerar la variance de reflect).
- **Gate:** `-m "not llm"` completo (reflect es cross-cutting) + `-m eval` **sin regresión** (verificar el caso `memory_answer`: con B, el turno del gate no re-guarda el answer del doc; el seed del caso sigue vía `_score_case`). `ruff`+`mypy app/` verdes.
- **Smoke navegador:** re-hacer test 2 con lenguaje natural ("en realidad ahora…" → "¿cuánto dura…?") **sin plantar nada** → debe dar la precedencia; forget sigue andando; HITL intacto.

## 8. Riesgos / gotchas
- **Turno-7:** revertir el eco confirmatorio (aceptado). *"corregí que X sin match"* → ack de chitchat + reflect guarda X (best-effort; si reflect timeoutea, se pierde — mismo best-effort de siempre + fast-follow #2 reflect-background lo mejora).
- **Tests de `memory_command`/e2e B-correct cambian de mecanismo** (memory_command→reflect): actualizarlos; la aserción del RESULTADO (45 sí, 30 no) se mantiene, pero ahora depende de reflect (más variance) → aserción al mecanismo + tolerancia.
- **Firma `reflect.run` (cross-cutting):** correr la suite COMPLETA `-m "not llm"` (lección Slice 12), no solo los archivos tocados.
- **`reflect` user-only** podría perder un hecho establecido solo en la aclaración del asistente (raro; aceptado).
- **reflect sigue best-effort + timeout 10s** (nunca rompe el turno); el juez de contradicción de Slice 14 a veces timeoutea (fast-follow #2).
- **`ruff format` antes de `ruff check`**; imports nuevos en tests al TOP (E402); mypy `app/` VERDE (pineado 1.13.*); Windows `dev.py` (no uvicorn); `test_vectorstore` wipea Qdrant → re-sembrar antes del smoke; front `:3100`, backend `:8000` (500 = dev.py caído).

## 9. Seams para después
- **DSPy** sobre router + `extract_command` + `gate`/`extract`/`judge_neighbor` (fiabilidad de clasificación — la causa raíz real de A).
- **reflect-background** (fast-follow #2, latencia) — con reflect como único escritor, mover reflect fuera del camino crítico gana más peso.
- Simplificar `extract_command` a "¿es forget?" (ya no necesita `correct`/`new_value`).
- Recall ponderado por recencia/salience (C).
