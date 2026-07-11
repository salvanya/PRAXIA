# Diseño — RAG memory-aware (paralelo + merge + precedencia de memoria)

> **Fase 2 · Fast-follow #1 (post-Memoria RICA)** · Fecha: 2026-07-07 · Estado: **aprobado (brainstorming)**, pendiente de `writing-plans`.
> Contrato: CLAUDE.md (local-first, $0, multi-tenant por `practice_id`, escrituras CRM solo por HITL, commits sin atribución a Claude). Diseño de referencia: `Praxia_Blueprint.md` (CRAG §4).
> Cierra una limitación **heredada del Slice 12** (integración memoria↔RAG): `recall` inyecta la memoria en `RagState` pero el subgrafo CRAG abstiene ignorándola cuando el dato no está en los documentos.

## 1. Contexto y problema

El Slice 12 tejió la memoria de largo plazo en todos los caminos del grafo. Para RAG, `recall_node` deja las memorias practice-scope en `state["memories"]` y `rag_node` las pasa al subgrafo (`initial_rag_state(..., memories=…)`). Pero el subgrafo **CRAG es doc-only en sus dos compuertas de abstención** → la memoria se thread-ea pero nunca se usa cuando el dato no vive en los documentos.

**Bug reproducible (smoke del Slice 14, 2026-07-07):** el usuario le contó a Praxia un dato (p. ej. "la seña vale $5000" → guardado en memoria por `reflect`). Turnos después pregunta *"¿cuánto vale la seña?"*:
1. **Router** → `rag` (parece documental; el router no puede saber que la respuesta está en memoria). Correcto.
2. **`recall_node`** recupera la memoria → `state["memories"]` la tiene. ✓
3. **`rag_node`** la pasa al subgrafo. ✓
4. **`grade_node`** corre `judge_relevance(original_query, reranked)` **solo contra chunks de documentos** → los docs no tienen el dato → `sufficient=False` → `reformulate` → reintenta → `abstain`.
5. **`synthesize_node` nunca corre** — y es el ÚNICO punto donde la memoria se usaría (`rag_subgraph.py::synthesize_node`, `memories=state.get("memories", [])`). El usuario ve `ABSTAIN_MESSAGE`: *"No encuentro esa información en los documentos disponibles."*

Aunque llegáramos a `synthesize`, dos cosas más lo condenan: el `SYSTEM_PROMPT` de síntesis dice *"respondé SOLO con la información de los fragmentos… si no está en los fragmentos, respondé exactamente '{ABSTAIN}'"* (contradice usar memoria), y `judge_groundedness(answer, reranked)` es doc-only → un dato que viene de memoria se juzga **alucinación** → `abstain`.

**Estado del código relevante:**
- `app/graph/rag_subgraph.py`: `RagState` **ya tiene `memories: list[dict]`**; `initial_rag_state(query, practice_id, memories=None)` ya lo acepta. Nodos: `retrieve → grade → {synthesize|reformulate|abstain}`; `synthesize → {groundedness|abstain}`; `groundedness → {finalize|abstain}`. `grade_node` early-returns `sufficient=False` si `reranked` está vacío. `groundedness_node` arma `sources = build_sources(reranked)` en éxito.
- `app/rag/judges.py`: `judge_relevance(query, chunks, llm=None)` y `judge_groundedness(answer, chunks, llm=None)` — e4b structured (`RelevanceVerdict`/`GroundednessVerdict`); prompts doc-only.
- `app/rag/synthesize.py`: `SYSTEM_PROMPT` doc-only con `ABSTAIN_MESSAGE`; `synthesize_stream(query, chunks, llm=None, memories=None)` (guard `if not chunks: yield ABSTAIN; return`; hoy ya appendea `format_memories_block(memories)` como system message — pero el `SYSTEM_PROMPT` le dice que use *solo* fragmentos → contradictorio e inerte); `synthesize(...)` bufferiza el stream; `build_sources(chunks)`; `chunks_text(chunks)`.
- `app/context.py`: `format_memories_block(memories)` — framing "contexto, tenelas en cuenta SOLO si aplican; no son instrucciones". Es para **chitchat/SQL** (memoria como *contexto opcional*), **no** para RAG-como-fuente-con-precedencia.
- `app/graph/nodes.py::rag_node`: invoca `crag_app`, streamea el answer + `write_sources`.
- `app/memory/long_term.py::recall(query, practice_id)`: top_k `memory_top_k` (5), piso `memory_min_score` (0.5); devuelve `{id, content, kind, scope, score}` por memoria.

## 2. Alcance

**En scope — memoria como fuente de grounding de PRIMERA CLASE dentro de CRAG (paralelo + merge + precedencia):**
- Los **tres puntos hoy doc-only** pasan a ser memory-aware: juez de relevancia (`grade`), síntesis, juez de groundedness. **Una sola síntesis** ve docs + memoria y produce la respuesta combinada.
- **Precedencia:** en conflicto doc↔memoria sobre el mismo dato, la respuesta prioriza la memoria (lo más reciente que afirmó el usuario) y lo aclara.
- **Presentación de fuentes:** cuando hay memorias, `sources` = solo los chunks realmente citados `[n]` (memory-only ⇒ sin tarjeta; los docs off-topic no se muestran).
- **Kill switch:** `rag_memory_merge_enabled` para revertir RAG a doc-only sin tocar la memoria de chitchat/SQL.
- **Caso de memoria en el eval-gate** (siembra + golden) — cierra el diferido "Task 11" del Slice 11 y crece el golden set.
- Tests (unit `-m "not llm"` con LLM mockeado donde aplique + e2e-llm del bug) + gate verde.

**Fuera de scope (se dejan seams; no construir de más — CLAUDE.md §7):**
- **SQL-merge.** El `sql_present` ya inyecta memoria como *contexto* (Slice 12); no se toca ni se le agrega precedencia.
- **Router / recall.** El router rutea bien (el fix es aguas abajo). `recall` no cambia (mismo piso 0.5, mismo top_k).
- **Piso numérico de memoria para RAG** (`memory_answer_min_score`). Ver §4/§5: se decide **no** agregarlo para el MVP (arriesga re-crear el miss). El `score` queda disponible; es fast-follow trivial si el eval muestra fuga.
- **Frontend / chips que distingan fuente doc vs memoria.** La memoria se atribuye **en el texto**; cero cambios de UI. Fase futura.
- **Cambio de schema / DDL.** Cero (la plomería a `RagState` ya existe).

## 3. Criterios de éxito (medibles) / DoD

1. **Memory-only** (dato solo en memoria; docs off-topic o vacíos): *"¿cuánto vale la seña?"* tras guardar "la seña vale $5000" ⇒ **no** devuelve `ABSTAIN_MESSAGE` y la respuesta **contiene el valor**, atribuido a memoria (sin `[n]`). e2e-llm (**test de regresión del bug**) + unit del subgrafo.
2. **Merge** (doc responde + memoria relevante): la respuesta **cita el doc `[n]`** *y* incluye el hecho de memoria atribuido. e2e/unit-llm; aserción de mecanismo (ambos presentes), no wording.
3. **Precedencia en conflicto** (doc dice X, memoria dice Y): la respuesta incluye Y (memoria) como el dato vigente y menciona la diferencia. Aserción de mecanismo: **ambos valores presentes** y el de memoria presente; frase de precedencia **relajada** (estilo Slice 13). unit-llm + e2e-llm.
4. **Fuentes:** memory-only ⇒ `sources == []` (sin tarjeta); merge ⇒ `sources` = solo chunks citados; **doc-only (sin memoria) ⇒ idéntico a hoy** (`build_sources(reranked)`). unit determinista de `select_sources`.
5. **No-regresión (invariante):** con `memories == []` los prompts/inputs de los tres puntos son **byte-idénticos a hoy** ⇒ gate `-m "not llm"` verde (**378** + nuevos), `-m eval` **sin regresión**, `ruff` + `mypy app/` verdes. El HITL de escrituras CRM **intacto** (no se toca ese camino). **Cero red saliente nueva** (Ollama/PG/Qdrant local).
6. **Multi-tenant:** la síntesis/jueces solo ven memorias de la práctica del turno (`recall` ya filtra por `practice_id`+`scope='practice'`; no se afloja).
7. **Sin llamadas nuevas al LLM** en ningún camino: los jueces ya existen, solo reciben más evidencia. Latencia ≈ igual (no es el fast-follow de latencia — ese es #2, reflect-background).

## 4. Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección | Razón |
|---|---|---|
| Precedencia docs↔memoria | **Paralelo + merge** (ambos alimentan una síntesis; se combinan citando ambas fuentes) | Elección del usuario. Valor real en el dominio: la memoria suele **actualizar** un documento estático (protocolo "45 min" vs "ahora 60"). |
| Conflicto sobre el mismo dato | **Precedencia de memoria** (lidera memoria, anota el doc) | Elección del usuario. Lo que el usuario afirmó es lo más reciente/vigente frente a un doc que puede estar viejo. |
| Dónde vive el fix | **Dentro de CRAG** (approach ①: memoria como grounding de 1ra clase en `grade`/`synthesize`/`groundedness`) | Una sola síntesis resuelve merge+precedencia en un prompt. ② (merge aislado en `rag_node`) y ③ (memoria-primero) multiplican llamadas al 12B y hacen la precedencia entre respuestas ya generadas torpe/frágil. |
| No-regresión del eval gate | **Invariante por branching:** `memories == []` ⇒ prompts/inputs idénticos a hoy | El gate no regresiona *por construcción*, no por probabilidad. Cada punto memory-aware ramifica y en la rama vacía usa los strings actuales literales. |
| Precisión de la memoria | **Sin piso numérico nuevo**; 4 capas semánticas (juez relevancia + prompt de síntesis que ignora memoria tangencial y abstiene + juez groundedness) | Un piso alto arriesga **re-crear** el miss que este fast-follow arregla. El juez semántico filtra mejor que un umbral de coseno. `score` disponible para un fast-follow si hace falta. |
| Presentación de fuentes | **Cited-only cuando hay memorias** (chunks cuyo `[n]` aparece en el answer); doc-only intacto | Memory-only ⇒ sin tarjeta (no mostrar docs off-topic como "fuente"). Doc-only sin memoria: se preserva el comportamiento actual (protege el gate y evita el caso "el 12B olvidó las marcas → sin fuentes"). |
| Seguridad ante kill | **`rag_memory_merge_enabled`** (default `True`) | La precedencia-sobre-docs es el comportamiento más riesgoso del sistema de memoria en un 12B local. Un flag revierte RAG a doc-only **sin** matar la memoria de chitchat/SQL (que usa otro camino). |

## 5. Arquitectura / componentes

### 5.1 Módulos modificados (superficie chica y testeable)
| Archivo | Cambio |
|---|---|
| `app/rag/judges.py` | `judge_relevance(query, chunks, memories=None, llm=None)` y `judge_groundedness(answer, chunks, memories=None, llm=None)`. **Branch:** `memories` vacío ⇒ prompt+human idénticos a hoy; con memoria ⇒ variante que suma la sección de memoria y ajusta la instrucción a "docs **o** memoria". |
| `app/rag/synthesize.py` | Reescritura del prompt de síntesis para merge+precedencia (§8.1). Memoria va como **evidencia etiquetada en el mensaje `human`** (no vía `format_memories_block`, que la enmarca como contexto opcional). **Branch** (memoria vacía ⇒ system+human actuales literales). Fix del guard `if not chunks and not memories`. **Nuevo** `select_sources(chunks, answer, memories) -> list[dict]`. |
| `app/graph/rag_subgraph.py` | `grade_node` y `groundedness_node` pasan `state["memories"]` a los jueces; `grade_node` ya no early-returns por `reranked` vacío si hay memorias (§8.2); `groundedness_node` usa `select_sources(reranked, answer, memories)` en vez de `build_sources(reranked)`. |
| `app/graph/nodes.py::rag_node` | Respeta `rag_memory_merge_enabled`: si `False`, pasa `memories=[]` al subgrafo (revierte a doc-only). |
| `app/config.py` | `rag_memory_merge_enabled: bool = True`. |
| `app/eval/` | Caso de memoria (siembra en el fixture + golden). |
| `backend/tests/` | Unit (jueces memory-aware, síntesis merge/precedencia/memory-only, `select_sources`, subgrafo) + e2e-llm del bug. |

**No se tocan:** `router`, `recall`/`long_term`, `context.py`, `sql_*`, `chitchat`, `reformulate`, el HITL de escrituras, ni el frontend. Sin deps nuevas, sin DDL.

### 5.2 Interfaces (unidades aisladas)
- `judge_relevance(query, chunks, memories=None) -> RelevanceVerdict` — "¿la **combinación** docs+memoria alcanza para responder?". Controla `synthesize` vs `reformulate` vs `abstain`.
- `judge_groundedness(answer, chunks, memories=None) -> GroundednessVerdict` — "¿cada afirmación está respaldada por docs **o** memoria?".
- `synthesize(query, chunks, memories=None) -> str` — respuesta combinada (docs `[n]` + memoria atribuida, precedencia en conflicto) o `ABSTAIN_MESSAGE`.
- `select_sources(chunks, answer, memories) -> list[dict]` — pura: sin memorias ⇒ `build_sources(chunks)`; con memorias ⇒ solo los `build_sources` cuyo `[n]` aparece en `answer`.

## 6. Estado — sin campos nuevos

`RagState.memories` y `AgentState.memories` ya existen (Slice 12). `initial_rag_state(query, practice_id, memories=None)` ya acepta el parámetro. No hay cambios a `state.py`/`RagState`. La única config nueva es `rag_memory_merge_enabled` (§5.1).

## 7. Flujo del subgrafo — topología intacta, memoria tejida en 3 nodos

```
recall (llena state.memories) → rag_node (memories=[] si !rag_memory_merge_enabled) → crag_app:

  retrieve → grade[judge_relevance(q, docs, memories)]
     sufficient? → synthesize[docs + memories, prompt con precedencia]
                    → (answer == ABSTAIN? → abstain)
                    → groundedness[judge_groundedness(ans, docs, memories)]
                        grounded? → finalize (sources = select_sources(docs, ans, memories))
                        else → abstain
     insufficient & quedan intentos → reformulate (docs) → retrieve
     insufficient & sin intentos → abstain
  → rag_node streamea answer + sources
→ consolidate (reflect + summary)  [intacto]
```
La topología (`build_crag`) **no cambia**: mismas aristas y nodos. Solo cambian los cuerpos de `grade_node`/`synthesize_node`/`groundedness_node` (qué evidencia ven) y `synthesize`/los jueces (prompts). `reformulate` no participa de la memoria (mejora la query de **docs**; la memoria no cambia entre reintentos).

## 8. Algoritmos

### 8.1 Síntesis memory-aware con precedencia (`synthesize_stream`, el punto de mayor apalancamiento)
**Rama con memoria** — system:
```
Sos el asistente de una práctica profesional. Respondé en español usando ÚNICAMENTE las
fuentes provistas: los FRAGMENTOS de documentos y los HECHOS que el usuario te indicó (memoria).
- Citá cada fragmento que uses con la marca [n].
- Los HECHOS de memoria NO llevan [n]; cuando uses uno, atribuílo en el texto (ej: "según me indicaste").
- Si un HECHO de memoria CONTRADICE un fragmento sobre el mismo dato, priorizá el hecho de
  memoria (es lo más reciente que te indicó el usuario) y aclará la diferencia
  (ej: "el protocolo indica 45 minutos, aunque me señalaste que ahora son 60").
- Usá un hecho de memoria SOLO si responde la pregunta; ignorá los que no apliquen.
- Si NI los fragmentos NI la memoria contienen la respuesta, respondé EXACTAMENTE: '{ABSTAIN_MESSAGE}'.
No inventes ni uses conocimiento externo.
```
human (con memoria):
```
Fragmentos:

{_format_context(chunks)}          # vacío si no hay chunks relevantes

Hechos que me indicaste (memoria):
- {m['content']}
- …

Pregunta: {query}
```
**Rama sin memoria (backward-compat):** system = `SYSTEM_PROMPT` actual literal; human = `f"Fragmentos:\n\n{_format_context(chunks)}\n\nPregunta: {query}"` (idéntico a hoy). El appendeo de `format_memories_block` de hoy **se elimina** (era inerte/contradictorio).

**Guard corregido:** `if not chunks and not memories: yield ABSTAIN_MESSAGE; return`. Con memorias presentes se procede a la síntesis aunque `chunks` esté vacío (habilita el **memory-only** cuando el retrieve no trajo docs relevantes).

Robustez en 12B: temp baja (0.1, como hoy), reglas imperativas con ejemplo. Es **candidato directo de DSPy** (próximo slice). El juez de groundedness memory-aware ataja invenciones fuera de docs+memoria.

### 8.2 `grade_node` memory-aware
```
async def grade_node(state):
    memories = state.get("memories", [])
    if not state["reranked"] and not memories:
        return {"sufficient": False}                 # idéntico a hoy (sin docs ni memoria)
    try:
        verdict = await judge_relevance(state["original_query"], state["reranked"], memories=memories)
        return {"sufficient": verdict.sufficient}
    except Exception:
        return {"sufficient": False}
```
- Sin memoria + `reranked` vacío ⇒ `sufficient=False` (idéntico a hoy).
- Sin memoria + `reranked` no vacío ⇒ `judge_relevance(q, reranked, memories=[])` ⇒ prompt idéntico a hoy (branch en el juez).
- Con memoria ⇒ el juez decide con la evidencia combinada. Si la memoria responde ⇒ `sufficient=True` en el intento 1 (sin reformular docs). Si ni docs ni memoria responden ⇒ `sufficient=False` ⇒ reformula docs (la memoria no cambia) ⇒ eventual abstención.

### 8.3 Jueces memory-aware (`judges.py`)
`judge_relevance`: la instrucción pasa a "sufficient=true si la respuesta se funda en los fragmentos **o** en la memoria"; el `human` suma "Memoria:\n{chunks-style de m['content']}" cuando hay memorias. `judge_groundedness`: "grounded=true si cada afirmación está respaldada por los fragmentos **o** por la memoria"; el `human` suma la sección de memoria. **Branch:** memorias vacías ⇒ system+human idénticos a hoy (verdicto idéntico ⇒ eval intacto).

### 8.4 `select_sources` (presentación)
```
def select_sources(chunks, answer, memories):
    if not memories:
        return build_sources(chunks)                 # doc-only: idéntico a hoy
    return [s for s in build_sources(chunks) if f"[{s['n']}]" in answer]
```
Memory-only (answer sin `[n]`) ⇒ `[]` ⇒ sin tarjeta. Merge ⇒ solo los citados. Trade-off asumido: si el 12B responde de docs pero **olvida** las marcas `[n]` *estando presente la memoria*, no se muestran fuentes (raro; mejorable con DSPy). El camino sin memoria conserva la red de seguridad actual (muestra `reranked`).

## 9. Configuración nueva (`config.py`)
| Campo | Default | Uso |
|---|---|---|
| `rag_memory_merge_enabled: bool` | `True` | Kill switch: `False` ⇒ `rag_node` pasa `memories=[]` al subgrafo ⇒ RAG doc-only (idéntico a pre-fast-follow), **sin** afectar la memoria de chitchat/SQL. |

Reusa: `memory_recall_enabled` (si el recall está off, `state["memories"]` viene vacío ⇒ doc-only por el invariante), `rag_max_attempts`/`rag_fetch_k`/`ollama_model`/`ollama_model_cheap` (sin cambios).

## 10. Errores / resiliencia / seguridad
- **Backward-compat airtight:** el invariante de branching (memorias vacías ⇒ strings actuales literales) hace que jueces/síntesis/fuentes se comporten idéntico a hoy en el 100% de los casos sin memoria. Es la garantía de no-regresión del gate/eval.
- **Fragilidad de la precedencia en 12B:** mitigada por prompt claro + temp baja + juez de groundedness memory-aware. Tests asertan **mecanismo** (valores presentes), no wording. DSPy es la vía de endurecimiento (próximo slice).
- **Fuga de memoria débil** (recall a 0.5 podría traer una memoria tangencial): filtran las 4 capas (juez de relevancia + "usá memoria solo si responde" + abstención + groundedness). Riesgo residual: groundedness verifica *grounding*, no *corrección* → una memoria off-topic mal usada pasaría groundedness. Aceptado para el MVP; el eval lo monitorea; `memory_answer_min_score` es el fast-follow si aparece.
- **Inyección vía memoria plantada** (la precedencia deja que un hecho de memoria sobreescriba un doc): la memoria es dato **de la práctica** (la escribe `reflect` desde la conversación o un comando explícito del propio usuario), no contenido de un documento subido no confiable. El usuario controla su memoria (`forget`/`corregí`). El framing "Hechos que me indicaste" la trata como **dato a usar**, no como instrucciones de sistema. Aceptado y documentado; endurecer guardrails de entrada es un slice posterior de Fase 2.
- **Multi-tenant:** `recall` ya filtra `practice_id`+`scope='practice'`; la síntesis/jueces solo reciben esas memorias. No se afloja nada.
- **Kill switch:** `rag_memory_merge_enabled=False` revierte a doc-only si la precedencia diera problemas en prod, sin tocar el resto de la memoria.

## 11. Testing
- **Unit** (`-m "not llm"`; LLM mockeado donde el veredicto/síntesis importe):
  - `select_sources`: sin memoria ⇒ `build_sources(chunks)` (todos); con memoria + answer que cita `[1]` ⇒ solo el chunk 1; memory-only (sin `[n]`) ⇒ `[]`.
  - `judge_relevance`/`judge_groundedness` (LLM mockeado): con `memories` el `human` incluye la sección de memoria y el system la cláusula "o memoria"; **sin** `memories` el system+human son idénticos a los actuales (assert de strings, protege el invariante).
  - `synthesize` (LLM mockeado que devuelve el prompt/echo o fake determinista): rama sin memoria ⇒ system=`SYSTEM_PROMPT` actual y human sin sección de memoria; rama con memoria ⇒ ambos bloques presentes; guard `not chunks and not memories` ⇒ `ABSTAIN`; `not chunks` **con** memorias ⇒ procede (no abstiene por el guard).
  - `rag_subgraph.grade_node`: `reranked=[]`+sin memoria ⇒ `sufficient=False` sin llamar al juez; `reranked=[]`+con memoria ⇒ llama al juez; excepción del juez ⇒ `sufficient=False`.
  - `rag_subgraph` (jueces/síntesis mockeados, sin Ollama): memoria relevante + docs off-topic ⇒ `abstained=False`, answer con el hecho; conflicto ⇒ answer con el valor de memoria; `groundedness=False` ⇒ `abstain`.
- **e2e-llm** (`-m llm`, Ollama+PG+Qdrant reales, `checkpointer=None`):
  - **Memory-only (regresión del bug):** sembrar memoria (real `store`) sin doc que la contenga → invocar el subgrafo/grafo con la pregunta → `abstained=False` y el valor presente (aserción dura del mecanismo).
  - **Merge:** doc sembrado con dato A + memoria con dato B (ambos relevantes) → answer cita `[n]` y contiene B atribuido.
  - **Precedencia:** doc "45 minutos" + memoria "60 minutos" → answer contiene "60" (memoria) y menciona la diferencia; aserción relajada del wording de precedencia.
- **Eval gate:** `-m eval` **sin regresión** (los golden actuales no traen memoria ⇒ invariante ⇒ idéntico). **Nuevo golden de memoria** (siembra una memoria en el fixture del gate; pregunta factual answerable-por-memoria; assert no-abstención + valor) → cierra "Task 11" y crece el golden set.
- **Gate `-m "not llm"`** verde (378 + nuevos); `ruff` + `mypy app/` verdes.
- **Smoke navegador** (Ollama+docker+seed+spaCy; **re-sembrar con `seed_demo.py` antes**, porque `-m "not llm"` wipea el Qdrant compartido): (1) contar un dato ("la seña vale $5000"), preguntar *"¿cuánto vale la seña?"* → responde el valor (no abstiene). (2) merge/precedencia con el `protocolo` demo: decir un override de algo del protocolo y preguntarlo → lidera la memoria y anota el doc. (3) una pregunta genuinamente documental sin memoria relacionada → sigue citando `[n]` como hoy. (4) una escritura CRM sigue abriendo la ConfirmCard (HITL intacto).

## 12. Seams para slices/fast-follows futuros
- **`memory_answer_min_score`** — piso numérico para elegibilidad de memoria en RAG, si el eval muestra fuga de memorias débiles (el `score` ya viaja en `state["memories"]`).
- **Chips de fuente doc vs memoria** en el canvas (hoy: atribución en texto). Frontend, fase futura.
- **DSPy** sobre el prompt de síntesis con precedencia y los jueces memory-aware, contra el golden set (incluye el nuevo caso de memoria).
- **Fast-follow #2 (reflect-background):** independiente; ataca la latencia del cierre de turno, no este camino.
- **Recall ponderado por recencia/salience** (diferido C de Memoria RICA): mejoraría la calidad de `state["memories"]` que alimenta este merge.

## 13. Riesgos / gotchas heredados a respetar en implementación
- **Lección Slice 12 (cross-cutting):** `judge_relevance`/`judge_groundedness`/`synthesize` cambian de firma (param `memories` opcional, backward-compatible) → **correr la suite completa `-m "not llm"`**, no solo los archivos tocados (la regresión de `_fake_synth` del Slice 12 solo la cazó el gate final).
- **Verificar el eval-harness real** antes de asumir cited-only: si algún assert determinista del gate cuenta `sources`, el cambio de `select_sources` solo aplica con memorias (doc-only intacto), así que no debería tocarlo — **confirmarlo** al implementar.
- **Structured-output e4b `None` intermitente:** los jueces ya son e4b structured; mantener el manejo actual (excepción ⇒ trato conservador: relevancia insuficiente / groundedness no-fundamentado).
- **`ruff format` antes de `ruff check`**; imports nuevos en tests existentes al TOP (E402).
- **`test_vectorstore` wipea el Qdrant compartido bajo `-m "not llm"`** → re-sembrar (`seed_demo.py`) antes del smoke con RAG; los tests de memoria crean/limpian sus propias memorias.
- **Windows + `dev.py`** (no uvicorn directo, ProactorEventLoop) para el smoke; front en `:3100`; si el chat da 500, chequear `dev.py` arriba en `:8000` (`/health`).
- **mypy `app/` VERDE** (pineado `1.13.*`) — no meter literales int ≥ 2^64 en `app/`.
