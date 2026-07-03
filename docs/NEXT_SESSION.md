# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño). Respetá: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id`, escrituras solo por tools con confirmación humana (HITL), commits LIMPIOS sin atribución a Claude. OJO: `CLAUDE.md` está GITIGNORED (setup en `backend/.env.example`).

2. **FASE 1 (MVP conversacional) CERRADA** — 10 slices en `origin`. **FASE 2 EN CURSO: Slice 1 (eval offline como GATE, merge `e4bb30f`, pusheado) y Slice 2 (MEMORIA DE LARGO PLAZO + reflexión + Context Manager MÍNIMO, merge LOCAL `5b328b5`, 2026-07-03) CERRADOS.** NO reabras nada de eso; el detalle vive en la memoria del proyecto. **OJO: Slice 2 es MERGE LOCAL, NO pusheado a `origin` todavía.**
   - SLICE 12 (Fase 2 #2) — Memoria LP: grafo `router → recall → {subgrafo} → reflect → END`. **Store/recall** scope `practice`: tabla `memories` (§5.2) + Qdrant `praxia_memories` (1024/COSINE, `content` en payload), dedup coseno 0.9; `app/memory/long_term.py` (store PG-first→Qdrant con compensación; recall filtro `practice_id`+`scope='practice'`). **Reflexión** `app/memory/reflect.py`: gate e4b (sí/no + `is_explicit` "acordate que…", sesgo a False) → extract e4b → dedup → store, **best-effort + timeout 10s (NUNCA rompe el turno)**. **Inyección** de memorias TRAS el prompt estable (framing anti-inyección) en chitchat / síntesis-SQL (NO el SELECT) / síntesis-RAG vía `app/context.py::format_memories_block`. Nodos `app/graph/memory_nodes.py`. `AgentState.memories`. Config `ollama_model_cheap` + 8 flags `memory_*`. **Arregla el pain del Slice 8** (memoria en TODOS los caminos). Task 11 (caso de memoria en el eval-gate) = DIFERIDA a fast-follow.

Estado (2026-07-03, HEAD `5b328b5` en main LOCAL, NO pusheado):
- Gates: `-m "not llm"` **323** (docker PG/Qdrant) · `-m eval` 3 · `-m llm` 22 (incl. 2 e2e de memoria cross-thread) · `-m pii` 5 · frontend 35.

Gotchas NUEVOS (Slice 12) + vigentes (detalle en la memoria del proyecto):
- **🔴 `mypy app/` CRASHEA (env PRE-EXISTENTE, NO de esta slice):** `TypeError: Integer exceeds 64-bit range` en `mypy/util.py:json_dumps` (write_cache), reproducible desde cache limpia, afecta los 48 archivos. **Verificá tipos por-archivo: `mypy <file> --follow-imports=skip` → "Success".** Arreglá el env (`pip install -U mypy` o localizá el int literal fuera de rango) antes de confiar en el gate `mypy app/` del DoD.
- **Lección de proceso:** cambios de firma CROSS-CUTTING → correr la SUITE COMPLETA `-m "not llm"`, no solo los test files tocados (una regresión en `test_graph.py::_fake_synth` con el kwarg `memories=` SOLO la cazó el gate final).
- **Latencia (fast-follow top):** `reflect` corre in-graph → el `done` SSE se retrasa hasta `memory_reflect_timeout_s`(10s) por turno de contenido → background reflection o bajar el timeout a 3-4s (TRADEOFF: menos captura de memoria en Ollama cargado — tu decisión).
- (previos) structured-output e4b None INTERMITENTE → text-parse/retry (args tipados 12b SÍ); imports nuevos en tests EXISTENTES al TOP (E402); `ruff format` ANTES de `ruff check`; backend con `dev.py` (no uvicorn directo, ProactorEventLoop); input plano a thread pausado en `confirm_action` descarta el interrupt; `test_vectorstore` wipea el Qdrant compartido bajo `-m "not llm"` (re-sembrá antes del smoke RAG); front en `:3100` (`:3000` = app de vinos).

Tarea: continuar FASE 2 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea + whole-branch. No construyas de más (CLAUDE.md §7); Fase 3/4 = fuera de alcance.

Alcance restante de Fase 2: **Context Manager COMPLETO** (`running_summary` + presupuesto de tokens + prefijo-estable para KV-cache; el mínimo ya está — `app/context.py` es el punto de ensamblado) · **DSPy** (MIPROv2/GEPA contra el golden set) · **caching** (semántico + embeddings) · **trazas Phoenix** · **guardrails ENDURECIDOS** (inyección con llm-guard, output safety, audit log — blueprint línea 556; el seam `G_OUT` va antes de `reflect`).

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código). Sugerencia de orden:
- **CONTEXT MANAGER COMPLETO (recomendado):** `running_summary` (resumen incremental de turnos viejos) + presupuesto de tokens + orden prefijo-estable para el KV-cache. Continúa directo lo de Slice 12 (el mínimo ya inyecta memorias; `context.py` es el hogar).
- GUARDRAILS ENDURECIDOS (inyección de prompt / output safety / audit log): sube el piso de seguridad; el seam `G_OUT` va antes de `reflect`.
- DSPy + caching + Phoenix: optimización/observabilidad — rinden con el eval-gate para medir la mejora.

Fast-follows fichados (NO bloquean; detalle en la memoria): **mypy env (tooling debt)**; latencia de cierre de turno (background reflection / bajar timeout); orphan PG si el timeout cancela mid-`store` (`try/except BaseException`); `_wipe` del e2e no limpia Qdrant; `long_term.recall` hard-key `payload["content"]`→`.get`; `test_build_wiring` sin edge-asserts; helpers `_run`/`_one_node_graph` a `conftest.py`; inyectar memorias en `propose_action`; **Task 11 = caso de memoria en el eval-gate**; + los previos (crecer golden set `cited_answer`, endurecer juez intención↔SQL, `when`/`new_start_at`→`astimezone(UTC)`, `_FIELD_LABELS` dup, `appt_resolve_limit`, denylist SQL, golden create↔cancel↔reschedule).
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 12 — Memoria de largo plazo + reflexión + Context Manager (MÍNIMO) (2026-07-03)
- **Mergeado LOCAL (`5b328b5`, `--no-ff`), NO pusheado a `origin` todavía.** Rama `fase2/memoria-lp` borrada. 14 commits (spec + plan + 10 tasks + fix T5 + fix de regresión). Arregla el pain del Slice 8: memoria "general" disponible en TODOS los caminos, no solo chitchat.
- **Diseño:** grafo `router → recall → {rag|sql|chitchat|action|scope_reject} → reflect → END` (`scope_reject → END` directo, salta reflect; el `interrupt` HITL sigue dentro de `confirm_action`, intacto). **Store/recall** scope `practice`: Postgres `memories` (fuente de verdad, +col `source∈{reflexion,explicito}`) + Qdrant `praxia_memories` (1024/COSINE, calca `praxia_chunks`, `content` en payload → recall sin join). `app/memory/long_term.py` (`store` PG-first→Qdrant con compensación; dedup coseno ≥0.9→None; `recall` filtro `practice_id`+`scope='practice'`, piso 0.5, top_k 5; `touch_last_used`; `ensure_memories_collection`). **Reflexión** `app/memory/reflect.py` (gate e4b sí/no+`is_explicit` sesgo-a-False → extract e4b cap 3 → dedup → store; best-effort + `asyncio.wait_for(10s)`, NUNCA rompe el turno; e4b None→retry ≤2x; pydantic sin underscore). **Inyección** `app/context.py::format_memories_block` (system message tras el prompt estable, framing anti-inyección) en chitchat + `sql_present` (síntesis, NO el SELECT) + síntesis RAG (threading por `RagState`). Nodos `app/graph/memory_nodes.py`. `AgentState.memories` (NO `running_summary` — diferido). `main.py` bootstrapea la colección. Config `ollama_model_cheap="gemma4:e4b"` + 8 flags `memory_*`.
- **Scope (brainstorming):** memoria LP end-to-end + Context Manager MÍNIMO. DIFERIDO: `running_summary`/token-budget/prefijo-KV (slice "Context Manager"), client/user-scope+PII, `G_OUT`, DSPy sobre gate/extract, inyección en `propose_action`, **Task 11 (caso de memoria en el eval-gate)** = fast-follow por decisión del usuario.
- **Reviews:** 10 task-reviews Approved + whole-branch opus (Ready=With fixes → 1 fix T5 `recall_node` nested-try) + regresión del gate final (`test_graph.py::_fake_synth` sin `memories=`) cazada+fija (`04bad7c`). Gate `-m "not llm"` **323 passed / 0 failed**, e2e-llm **2** (cross-thread + aislamiento). Spec/plan en `docs/superpowers/{specs,plans}/2026-07-03-memoria-lp-context-manager*`. Ledger SDD en `.superpowers/sdd/progress.md` (gitignored).
- **🔴 GOTCHA CRÍTICO (env, PRE-EXISTENTE):** `mypy app/` CRASHEA (`Integer exceeds 64-bit range` en write_cache, afecta los 48 archivos, reproducible desde cache limpia). Verificar por-archivo con `mypy <file> --follow-imports=skip`. Rompe el gate `mypy app/` del DoD → arreglar el env.

### Cierre Slice 11 — Suite de eval offline como GATE (FASE 2 arranca) (2026-07-02)
- **Mergeado (`e4bb30f`, `--no-ff`) y PUSHEADO a `origin`.** Rama `fase-2/slice-eval-offline-gate` borrada. 14 commits (spec + plan + pivot-docs + 8 tasks + 3 fix-waves post-review). **Smoke navegador VALIDADO (2026-07-02):** chitchat sin artefactos; RAG con cita (60 min del protocolo sembrado); abstención sin fuentes (dirección); tabla SQL + toggle; escalar sin tabla; HITL create/cancel.
- **Diseño:** el gate corre el golden set (`backend/app/eval/golden_set.jsonl`) end-to-end por el grafo real y decide pass/fail = aserciones deterministas por-caso [DURO] + 4 métricas por LLM-as-judge con baseline-diff [REGRESIÓN]. Módulos `app/eval/{cases,checks,baseline,harness,metrics,run,fixtures}.py` + `baseline.json` (committeado, 5 métricas=1.0) + `README.md`. Marker `eval`; tests Ollama/PG = `eval`+`llm` (fuera de `-m "not llm"`).
- **PIVOT (decisión del usuario):** Ragas resultó dependency-incompatible con el stack congelado de Fase 1 → **jueces LLM-as-judge locales** (`gemma4:12b`, reusando `rag/judges.py::judge_groundedness` para faithfulness + 3 jueces booleanos para relevancy/precision/recall; `score_rag_cases` async). Cero deps nuevas.
- **Reliability fix (systematic-debugging):** `test_vectorstore.py` hace `delete_collection` y corre bajo `-m "not llm"` → la suite fast destruía el corpus RAG del gate. Fix: el gate auto-siembra su fixture (`fixtures.py`), y `vectorstore.count_chunks` devuelve 0 si la colección no existe. `seed_demo.py` ingesta un `protocolo` demo (el corpus RAG que Fase 0 no sembraba).
- **Reviews:** 7 task-reviews Approved + whole-branch opus (Ready=YES) + delta opus (Ready=YES). Spec/plan en `docs/superpowers/{specs,plans}/2026-07-02-eval-offline-gate*`.

### Cierres previos (Fase 1 CERRADA — detalle en la memoria del proyecto y en `docs/superpowers/`)
10 slices: grafo+router (`ae46438`) · CRAG (`d765eca`) · NL2SQL read-only (`8804a73`) · create_appointment HITL (`8e0ccfd`) · log_interaction+registry (`5138844`) · cancel (`f3d520b`) · reschedule+update_client (`494a511`) · memoria corto+slot-filling (`1739713`) · guardrails PII (`6004277`/`31f124f`) · canvas rico inline (`171bffd`). Todos mergeados+pusheados, smoke navegador validado.

### Comandos útiles
```bash
docker compose up -d                                                       # postgres + qdrant
backend\.venv\Scripts\python backend\seed_demo.py                          # 3 prof, 30 clientes, 80 turnos + doc protocolo
backend\.venv\Scripts\python backend\dev.py                                # backend :8000 (NO uvicorn directo)
npm --prefix frontend run dev -- --port 3100                               # front en :3100 (:3000 lo ocupa el proyecto de vinos)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q       # 323 passed (docker arriba)
cd backend && .venv\Scripts\python -m app.eval.run                         # EVAL GATE (Ollama+PG+Qdrant+seed); --update-baseline p/ re-fijar
backend\.venv\Scripts\python -m pytest backend/tests -m eval -q            # 3 passed (wrapper del gate + smoke métricas + self-heal)
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q             # 22 passed (incl. 2 e2e de memoria cross-thread; requiere Ollama)
# ⚠️ mypy app/ CRASHEA (bug de tooling pre-existente); verificá por-archivo:
cd backend && .venv\Scripts\python -m mypy app/<archivo>.py --follow-imports=skip
ollama list                                                                # gemma4:12b y gemma4:e4b
```
> Smoke navegador: Ollama + docker + schema/seed + spaCy `es_core_news_md`. **OJO: la suite fast (`-m "not llm"`) wipea el Qdrant compartido → re-sembrá (`seed_demo.py`) antes del smoke con RAG.** El front de Praxia va en `:3100` (`:3000` = app de vinos). Checklist en `frontend/SMOKE.md`. **Smoke de memoria:** decí "acordate que los turnos duran 30 minutos" (chitchat), reiniciá el thread, y preguntá algo relacionado → la memoria debe influir; las escrituras siguen abriendo la ConfirmCard (HITL intacto).
