# Diseño — Suite de eval offline como gate (arranque Fase 2)

- **Fecha:** 2026-07-02
- **Fase:** 2 (Optimización y confiabilidad) — **primer slice**
- **Estado:** aprobado en brainstorming; pendiente de plan (writing-plans)
- **Slice previo:** Slice 10 — canvas rico (`171bffd`, cierre Fase 1)

## 1. Contexto y objetivo

Fase 1 quedó cerrada: grafo+router, CRAG, NL2SQL read-only, 5 write-tools con HITL, memoria
corto plazo + slot-filling, guardrails PII y canvas rico. CLAUDE.md §6 **ya referencia** una
"suite offline de eval" como *gate* de mergeo ("Si tocaste retrieval/SQL/síntesis/router: la
suite offline de eval no regresiona"), pero **hoy no existe**: `backend/app/eval/` contiene
únicamente `golden_set.jsonl` con **4 casos** (2 RAG, 2 SQL). No hay runner, ni jueces
offline, ni integración con Ragas. `python -m backend.eval.run` (CLAUDE.md §2) es aspiracional.

**Objetivo:** construir esa suite. Corre el golden set **end-to-end por el grafo real**,
captura `AgentState`, y emite un veredicto pass/fail que combina **aserciones deterministas
por-caso** (gate duro) con **métricas Ragas agregadas comparadas contra un baseline
committeado con tolerancia** (gate de regresión). Es el cimiento de confiabilidad del resto de
Fase 2: sin baseline medible no se puede probar que DSPy mejore nada ni que memoria / context
manager / caching no regresionen retrieval o SQL (criterio de aceptación del blueprint, línea
557: "mejora medible post-DSPy vs. baseline").

## 2. Decisiones (tomadas en brainstorming)

1. **Modelo de ejecución: end-to-end por el grafo real.** Cada caso entra como mensaje de
   usuario, corre router → subgrafo → jueces online; se captura el `AgentState` final y se
   puntúa sobre él. Cubre router + integración + citas (lo que §6 exige). Lento y
   no-determinista, pero es **gate manual pre-merge** (clase `-m llm`), no el loop rápido.
2. **Motor de métricas: Ragas apuntado a Ollama** (no jueces caseros). LLM interno de Ragas =
   `gemma4:12b`; embeddings = `bge-m3` local. Métricas: faithfulness, answer_relevancy,
   context_precision, context_recall.
3. **Semántica del gate: híbrida.** Aserciones deterministas por-caso = gate **duro** (bloquea
   siempre). Ragas agregado + execution-accuracy = gate de **regresión** (baseline-diff con
   tolerancia).
4. **LM del juez de Ragas: `gemma4:12b`** (métricas más estables → baseline confiable). El
   `e4b` sigue para los jueces *online* del grafo; las aserciones deterministas no usan LLM.
5. **Embeddings de Ragas (sub-decisión A1):** adapter fino que **reusa el mismo
   `SentenceTransformer` bge-m3 ya cargado** en `app/embeddings.py` (no recarga ~2 GB).
6. **Interfaz (sub-decisión B1):** CLI runner (`python -m app.eval.run`, exit 0/1,
   `--update-baseline`) **+** un wrapper pytest fino marcado `eval` que entra en el ritual
   `pytest` y en el DoD.

## 3. No-goals (fuera de alcance de este slice)

- **Phoenix / trazas** (otro slice de Fase 2).
- **DSPy, memoria largo plazo, caching** (otros slices de Fase 2).
- **Casos de acción/escritura en el golden set.** El camino write usa `interrupt` + resume y
  necesita checkpointer; hoy el set es RAG+SQL de lectura y `get_default_graph()` (sin
  checkpointer) alcanza. Extender a acciones = fast-follow (§11).
- **Ragas como reporte enriquecido más allá del gate** (dashboards, por-caso persistido a DB
  `eval_cases`). El golden set vive como **JSONL versionado** (di'able, crece con cada bug),
  no en la tabla Postgres `eval_cases` (que queda para Fase 4 si hace falta).
- **CI en la nube.** El gate es **local, manual, $0** (como la suite `-m llm` actual). Nada de
  GitHub Actions ni servicios cloud.
- **Endurecer el juez intención↔SQL / denylist SQL / UTC / audit log:** fast-follows ya
  fichados; este slice los *mide* (los casos pueden exponerlos) pero no los arregla.

## 4. Arquitectura — flujo por caso

```
EvalCase (línea de golden_set.jsonl)
   │
   ▼  get_default_graph().ainvoke(new_state(question, DEMO_PID, fresh_thread_id))
AgentState final
   │
   ▼  harness.run_case → CaseResult {
   │      intent, answer = (último AIMessage).content,
   │      retrieved: list[Chunk], sources: list[dict], candidate_sql: str }
   │
   ├─► checks.py — aserciones deterministas (gate DURO, sin LLM):
   │      · intent == case.intent
   │      · behavior "cited_answer"      → len(sources) > 0 y todos los must_include presentes en answer
   │      · behavior "abstain_no_sources"→ answer contiene la frase de abstención y NO hay sources
   │      · category "sql"               → intent=="sql", candidate_sql es un SELECT, y
   │                                        EXECUTION-ACCURACY: run_select(gold_sql) ≟ run_select(candidate_sql)
   │
   └─► ragas_metrics.py — solo casos RAG "cited_answer":
          dataset = {question, answer, contexts=[c["text"] for c in retrieved], ground_truth}
          evaluate(dataset, [faithfulness, answer_relevancy, context_precision, context_recall],
                   llm=LangchainLLMWrapper(make_llm("gemma4:12b",0.0)),
                   embeddings=LangchainEmbeddingsWrapper(BgeM3Adapter()))
          → agregados por métrica
                       │
                       ▼
   baseline.py: agregados Ragas + execution-accuracy(%)  vs  baseline.json (committeado)
                → regresión si alguna métrica cae > tolerancia
```

`ainvoke` (no `astream`): quiero el estado final mergeado. Los campos
`retrieved/sources/candidate_sql/intent/messages` llegan como state-updates; la respuesta
final es `messages[-1].content`. Los eventos SSE (`token/sources/table`) son para la UI; acá
se lee el state directo.

**Alternativas descartadas** (ya en brainstorming): component-level (no cubre router);
híbrido (dos caminos de ejecución); jueces caseros como gate (se eligió Ragas); umbrales
absolutos / solo-baseline (se eligió híbrido determinista + baseline-diff).

## 5. Contrato de datos — golden set (schema extendido)

Ragas `context_precision`/`context_recall` necesitan una **respuesta de referencia**. Se
agrega `ground_truth` a los casos RAG `cited_answer`, y `intent` explícito a todos (para la
aserción de router). Los `abstain` y los `sql` **no** llevan `ground_truth` (Ragas no aplica).

```jsonc
// RAG cited (extendido)
{"question":"¿cuánto dura la primera consulta?","category":"rag","intent":"rag",
 "expected_behavior":"cited_answer","must_include":["60"],
 "ground_truth":"La primera consulta dura 60 minutos."}            // ← NUEVO (referencia)
// RAG abstain (agrego intent; Ragas no aplica)
{"question":"¿cuál es la dirección del consultorio?","category":"rag","intent":"rag",
 "expected_behavior":"abstain_no_sources","must_include":["No encuentro esa información"]}
// SQL (agrego intent; execution-accuracy usa gold_sql que ya está)
{"question":"¿cuántos turnos hay esta semana?","category":"sql","intent":"sql",
 "expected_behavior":"sql_answer",
 "gold_sql":"SELECT count(*) FROM appointments WHERE practice_id = '…0001' AND …"}
```

`cases.py` define `EvalCase` (dataclass) y **valida por categoría**: falla ruidoso si a un
`cited_answer` le falta `ground_truth`/`must_include`, o a un `sql` le falta `gold_sql`.
Campos: `question`, `category` ∈ {rag, sql}, `intent`, `expected_behavior` ∈
{cited_answer, abstain_no_sources, sql_answer}, `must_include: list[str]`,
`ground_truth: str | None`, `gold_sql: str | None`, `seed_doc: str | None` (informativo).

> El **vocabulario exacto de `intent`** (qué string emite `state["intent"]`) se toma de
> `graph/router.py` al escribir `cases.py` — **no se inventa**. La aserción de router compara
> `CaseResult.intent == case.intent` con ese vocabulario real (p. ej. `rag`/`sql`; confirmar).

## 6. Módulos nuevos (`backend/app/eval/`)

| Archivo | Propósito (una cosa) | Reusa |
|---|---|---|
| `golden_set.jsonl` | Datos (schema §5). Arranca chico, crece con cada bug. | — |
| `cases.py` | `EvalCase` + loader/validador del JSONL. Puro. | — |
| `harness.py` | Corre **un** caso end-to-end → `CaseResult`. | `graph.build.get_default_graph`, `graph.state.new_state`, `config.settings.practice_id` |
| `checks.py` | Aserciones deterministas por-caso, incl. execution-accuracy. Puras sobre `CaseResult`. | `db.run_select` |
| `ragas_metrics.py` | Wrappers Ragas (LLM 12b + `BgeM3Adapter`), dataset, `evaluate()` → agregados. | `llm.make_llm`, `embeddings` |
| `baseline.py` | load/save/diff de `baseline.json` con tolerancia. Puro. | — |
| `run.py` | CLI: orquesta, imprime reporte, escribe `last_run.json`, exit code. Flags. | todo lo anterior |

Artefactos de datos: `baseline.json` (**committeado**, referencia de regresión) y
`last_run.json` (**gitignored**, resultado efímero de la última corrida).

### 6.1 `harness.py` (contrato)

```python
@dataclass
class CaseResult:
    case: EvalCase
    intent: str
    answer: str                 # messages[-1].content (str)
    retrieved: list[Chunk]      # Chunk = {text,page,chunk_index,document_id,title,doc_type}
    sources: list[dict]         # {n,title,page,document_id}
    candidate_sql: str

async def run_case(case: EvalCase, graph=None) -> CaseResult:
    graph = graph or get_default_graph()
    state = await graph.ainvoke(new_state(case.question, settings.practice_id, uuid4().hex))
    return CaseResult(case=case, intent=state["intent"],
                      answer=_last_ai_text(state), retrieved=state["retrieved"],
                      sources=state["sources"], candidate_sql=state["candidate_sql"])
```

`_last_ai_text` toma el último `AIMessage` y normaliza `content` a `str` (defensivo, como
`last_user_text` en `state.py`). Cada caso usa un `thread_id` fresco (aislamiento; no comparte
checkpoint).

### 6.2 `checks.py` (execution-accuracy)

`run_select(sql, timeout_ms=settings.sql_timeout_ms, row_limit=settings.sql_row_limit)`
devuelve `(rows: list[dict], columns: list[str])` en transacción **read-only** con timeout y
row-limit ya garantizados. Execution-accuracy = **igualdad de multiset de filas por VALORES**
(ignora nombres/alias de columna, respeta multiplicidad):

```python
def _canon(rows: list[dict]) -> Counter:
    # cada fila → tupla ordenada de valores stringificados (default=str p/ datetime/Decimal/UUID)
    return Counter(tuple(sorted(str(v) for v in row.values())) for row in rows)

async def execution_accuracy(gold_sql: str, candidate_sql: str) -> bool:
    g, _ = await run_select(gold_sql, ...)
    c, _ = await run_select(candidate_sql, ...)
    return _canon(g) == _canon(c)
```

Para el caso escalar (`count(*)`, 1×1) se reduce a comparar el único valor. Si el candidato
está vacío/None (abstención SQL) → falla la aserción (correcto). La normalización por valores
tolera que el candidato aliasee distinto la columna.

### 6.3 `ragas_metrics.py` (adapter A1)

```python
class BgeM3Adapter(Embeddings):           # langchain_core.embeddings.Embeddings (sync)
    def embed_documents(self, texts): return _shared_model().encode(texts).tolist()
    def embed_query(self, text):      return _shared_model().encode([text])[0].tolist()
```

`_shared_model()` reusa el **mismo** `SentenceTransformer` bge-m3 singleton de
`app/embeddings.py`. Hoy `embeddings.py` expone API async (`embed_query`/`embed_texts`); si el
singleton no es accesible como sync, se agrega un accessor mínimo al singleton (**no** recarga
el modelo). LLM de Ragas = `LangchainLLMWrapper(make_llm("gemma4:12b", 0.0))`.

> **Nombres a confirmar en el spike (Tarea 1), NO inventar:** versión de `ragas`; paths de
> import de las métricas; builder de dataset (`Dataset.from_list` vs `EvaluationDataset` /
> `SingleTurnSample`); nombres de campos (`ground_truth` vs `reference`, `contexts` vs
> `retrieved_contexts`, `question` vs `user_input`, `answer` vs `response`). El spec fija la
> **intención**; el spike fija la **API exacta** contra la versión pineada.

### 6.4 `run.py` (CLI)

`cd backend && .venv\Scripts\python -m app.eval.run`. Flags: `--update-baseline`
(re-snapshotea `baseline.json` tras mejora intencional), `--only rag|sql`, `--tolerance
<float>` (default `0.05`). Reporte a consola: tabla por-caso (pass/fail + valores de métrica)
+ resumen de regresión. Exit **0** si (0 fallos duros) ∧ (0 regresión); **1** en caso
contrario. Escribe `last_run.json`.

## 7. Semántica del gate (detalle)

- **Duro (siempre bloquea):** cualquier aserción determinista fallida → exit≠0. No depende de
  Ragas ni del ruido del 12B.
- **Regresión (bloquea si cae > tolerancia):** por cada métrica agregada (mean de faithfulness,
  answer_relevancy, context_precision, context_recall sobre casos RAG cited; execution-accuracy
  = fracción de casos SQL correctos) se compara `baseline - actual > tolerancia`. Default
  tolerancia `0.05` absoluto; arranca ancha (4 casos = ruidoso) y se endurece a medida que el
  set crece (CLAUDE.md §6: "agregá casos cuando arregles un bug").
- Todo con `temperature=0`. `--update-baseline` es el ritual explícito para mover el baseline
  (se **committea** el nuevo `baseline.json`).
- **Primera corrida (sin `baseline.json`):** el diff se saltea (no marca regresión), el
  reporte lo avisa y sugiere `--update-baseline` para snapshotear la línea base inicial. Las
  aserciones duras **sí** corren igual (no dependen del baseline).

## 8. Dependencias y setup

- **`ragas`** se agrega a `backend/requirements.txt` con versión pineada **decidida en el
  spike** (Tarea 1). Verificar que la versión moderna hace LLM/embeddings pluggables y que
  `openai` es **opcional** (no import obligatorio). Traerá transitivos (`datasets`, `pandas`,
  `numpy`) — anotar en el spike.
- Pins actuales a respetar: `langchain-ollama==0.2.*`, `langgraph==0.2.*`,
  `sentence-transformers==3.*`, `pydantic-settings==2.*`. Confirmar que `ragas` no fuerza un
  `langchain-core` incompatible con esos.
- **Marker `eval`** nuevo en `backend/pyproject.toml` `[tool.pytest.ini_options].markers`
  (superset de `integration`+`llm`; **no** corre bajo `-m "not llm"`).
- **`.gitignore`:** agregar `backend/app/eval/last_run.json`.
- **Invocación documentada** en `backend/.env.example` / `frontend/SMOKE.md` (CLAUDE.md está
  gitignored): el path real es `app.eval.run` (no `backend.eval.run`).

## 9. Testing (gates de DoD)

### 9.1 Tests rápidos (corren en `-m "not llm"`, sin LLM/PG/Qdrant)
- `cases.py`: loader parsea el JSONL; validación falla ruidoso ante campos faltantes por
  categoría.
- `checks.py`: cada aserción con `CaseResult` sintético (cited ok/faltando must_include/sin
  sources; abstain ok/con sources; intent mismatch). `_canon`/execution-accuracy con
  `list[dict]` en memoria (mismo multiset con orden distinto → igual; alias de columna → igual;
  fila de más/de menos → distinto). **La comparación de result-sets se testea sin DB** (función
  pura); el `run_select` real se cubre en la suite `eval`.
- `baseline.py`: diff detecta regresión > tolerancia; no marca dentro de tolerancia;
  `--update-baseline` reescribe.
- `harness.run_case`: con el **grafo mockeado** (un stub que devuelve un `AgentState`
  prefabricado) mapea state→`CaseResult` correcto. No toca Ollama.

### 9.2 Suite `eval` (marcada `@pytest.mark.eval`, manual, contra docker+Ollama)
- `tests/test_eval_gate.py` (wrapper B1): llama al core del runner sobre el golden set real y
  asserta **0 fallos duros** y **0 regresión** vs baseline. Es el gate en formato pytest.
- Requiere `docker compose up -d` + seed demo + Ollama con ambos modelos + spaCy (por el
  camino PII de `log_interaction`, aunque el set actual no lo dispare).

### 9.3 Meta-gate
- El gate `-m "not llm"` **no regresiona** (272 + los nuevos tests puros).
- La suite `eval` corre a mano, produce `baseline.json` inicial (se committea) y `last_run.json`.

## 10. Definition of Done

1. `ruff format` → `ruff check` → `mypy --config-file backend/pyproject.toml` limpios (nota:
   `ruff format` **antes** de `check`; imports nuevos en tests existentes al top por E402).
2. Gate `-m "not llm"` no regresiona; nuevos tests §9.1 pasan.
3. `ragas` pineado e importable en el venv; el spike (Tarea 1) documentó la API real.
4. `python -m app.eval.run` corre end-to-end contra docker+Ollama, imprime reporte y genera
   `baseline.json` (committeado); `pytest -m eval` pasa.
5. Local-first/$0 intacto: LM local (12b), embeddings local (bge-m3), **cero red saliente
   nueva** del producto; Ragas es OSS self-hosteado.
6. Aislamiento multi-tenant intacto: todos los casos corren con `settings.practice_id`; el
   grafo filtra por `practice_id` como siempre.
7. Commit(s) limpios, **sin atribución a Claude**.

## 11. Riesgos y mitigaciones

1. **(El grande) Compatibilidad de `ragas`.** No está en `requirements.txt`; la API cambió
   entre 0.1 y 0.2. → **Tarea 1 = spike aislado:** pinear versión, smoke-importar (sin
   `openai` obligatorio), confirmar API de métricas/dataset/campos **antes** de escribir
   `ragas_metrics.py`. Si hay conflicto irreconciliable con los pins actuales → **freno y
   aviso** (no cambio la decisión Ragas por mi cuenta).
2. **No-determinismo del 12B** en las métricas Ragas → tolerancia en el baseline-diff +
   `--update-baseline` + `temperature=0`. Arrancar la tolerancia ancha.
3. **Doble carga del modelo bge-m3** (memoria) → mitigado por A1 (adapter reusa el singleton).
4. **`ainvoke` sobre el camino de escritura** requeriría checkpointer → el golden set es
   RAG+SQL de lectura; `get_default_graph()` sin checkpointer alcanza. Extender a acciones =
   fast-follow (necesitaría checkpointer + simular el resume).
5. **Lentitud de la suite** (LLM end-to-end por caso) → es gate **manual pre-merge**, no el
   loop rápido; los tests puros de §9.1 sí corren rápido. `--only rag|sql` para iterar.
6. **El juez intención↔SQL a veces aprueba un SELECT arbitrario** (fast-follow abierto) → la
   execution-accuracy determinista lo **expone** (candidate ≠ gold), que es justamente el
   valor del gate; arreglarlo es otro slice.

## 12. Fast-follows (no bloquean)

- Crecer el golden set con cada bug (create↔cancel↔reschedule, denylist SQL, casos de
  abstención adicionales).
- Casos de **acción/escritura** en el set (checkpointer + simular resume).
- **Ragas como reporte** más allá del gate (histórico por-caso, tendencias) — natural cuando
  entre **Phoenix** (otro slice de Fase 2).
- Persistir corridas a `agent_runs`/`eval_cases` (audit) — se cruza con el slice de guardrails
  endurecidos (audit log).
- Endurecer la tolerancia y añadir umbrales absolutos a medida que el set madura.

## 13. Secuencia sugerida (para writing-plans)

1. **Spike Ragas (de-riesga todo):** agregar `ragas` pineado, smoke-import, confirmar
   API/campos, `BgeM3Adapter` + `LangchainLLMWrapper` con un `evaluate()` mínimo de humo.
2. **`cases.py`** (EvalCase + loader + validación) + tests puros. Extender `golden_set.jsonl`
   (§5).
3. **`checks.py`** (aserciones + execution-accuracy pura) + tests puros.
4. **`baseline.py`** (load/save/diff) + tests puros.
5. **`harness.py`** (run_case end-to-end) + test con grafo mockeado.
6. **`ragas_metrics.py`** (dataset + evaluate, sobre lo confirmado en el spike).
7. **`run.py`** (CLI + reporte + exit code) + marker `eval` + `.gitignore` + `test_eval_gate.py`.
8. **Corrida real** contra docker+Ollama: generar y committear `baseline.json`; documentar
   invocación; gate completo.
