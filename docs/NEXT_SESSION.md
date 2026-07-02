# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño). Respetá: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id`, escrituras solo por tools con confirmación humana (HITL), commits LIMPIOS sin atribución a Claude. OJO: `CLAUDE.md` está GITIGNORED (setup en `backend/.env.example`).

2. **FASE 1 (MVP conversacional) CERRADA** — 10 slices en `origin`, smoke navegador validado. **FASE 2 EN CURSO: Slice 1 (suite de eval offline como GATE) CERRADO** (merge `e4bb30f`, 2026-07-02, pusheado, smoke navegador validado). NO reabras nada de eso; el detalle completo vive en la memoria del proyecto.
   - SLICE 11 (Fase 2 #1) — Suite de eval offline como GATE: `backend/app/eval/{cases,checks,baseline,harness,metrics,run,fixtures}.py` corre `golden_set.jsonl` **end-to-end por el grafo real** → **aserciones deterministas duras** (intent · citas/abstención · `must_include` · execution-accuracy SQL = igualdad de multiset de filas por valores) + **4 métricas por LLM-as-judge local (12b)** con baseline-diff de regresión. Invocación: `cd backend && .venv/Scripts/python -m app.eval.run` (`--update-baseline`/`--only`/`--tolerance`) + wrapper `pytest -m eval`. **PIVOT clave: Ragas DESCARTADO** (incompat de deps con el stack de Fase 1, en AMBAS direcciones) → jueces LLM locales reusando `rag/judges.py`, **cero deps nuevas**. **El gate AUTO-SIEMBRA su fixture RAG** (`fixtures.py::ensure_rag_fixture`) porque `tests/test_vectorstore.py` wipea el Qdrant compartido bajo `-m "not llm"`. `seed_demo.py` ahora ingesta un `protocolo` demo.

Estado (2026-07-02, todo pusheado a origin, HEAD `e4bb30f`):
- Gates: `-m "not llm"` **299** (docker Postgres/Qdrant) · `-m eval` **3** (Ollama+PG+Qdrant+seed) · `-m llm` 20 · `-m pii` 5 · frontend 35. baseline eval = 5 métricas en 1.0.

Gotchas NUEVOS (Slice 11) + vigentes (detalle en la memoria del proyecto):
- **Ragas es incompatible con el stack de Fase 1** (0.2.x→langchain-core 1.x; 0.1.x→core 0.2.43+openai) → métricas por jueces LLM locales; NO reintentar Ragas sin migrar a langchain 1.x (Fase 4+). El modelo pydantic del juez NO puede tener underscore (`_YesNo` rompe el structured-output de Gemma → `YesNoVerdict`).
- **`test_vectorstore.py` wipea el Qdrant compartido bajo `-m "not llm"`** → antes de un smoke navegador con RAG, re-sembrá (`seed_demo.py`); el eval gate se auto-siembra solo. `--only rag|sql` ya NO da falsas regresiones (comparaba dict parcial vs baseline completo).
- **`:3000` lo ocupa OTRO proyecto tuyo** (app de vinos "seisluces/vinodivino", devuelve 307 i18n) → corré el front de Praxia en puerto dedicado: `npm --prefix frontend run dev -- --port 3100`.
- (previos) structured-output e4b None INTERMITENTE → router/classify_write a text-parse; imports nuevos en tests EXISTENTES al TOP (E402); `ruff format` ANTES de `ruff check`; mypy `--config-file backend/pyproject.toml`; backend con `dev.py` (no uvicorn directo, ProactorEventLoop); input plano a thread pausado en `confirm_action` descarta el interrupt.

Tarea: continuar FASE 2 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea + whole-branch. No construyas de más (CLAUDE.md §7); Fase 3/4 = fuera de alcance.

Alcance restante de Fase 2: **DSPy** (MIPROv2/GEPA contra el golden set) · **memoria largo plazo + reflexión** (tabla `memories` + Qdrant, recuperación por coseno) · **Context Manager** (prefijo estable + `running_summary` + memorias en TODOS los caminos, no solo chitchat) · **caching** (semántico + embeddings) · **trazas Phoenix** · **guardrails ENDURECIDOS** (inyección con llm-guard, output safety, audit log — blueprint línea 556).

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código). Sugerencia de orden:
- **CONTEXT MANAGER + memoria largo plazo/reflexión (recomendado):** la memoria "general" en TODOS los caminos (no solo chitchat) — destapado como limitación del Slice 8 ("¿quién es mi profesional?" rutea a `sql` y no usa la memoria de chitchat, y el Data Agent a veces inventa). Cimiento del resto.
- GUARDRAILS ENDURECIDOS (inyección de prompt / output safety / audit log): sube el piso de seguridad.
- DSPy + caching + Phoenix: optimización/observabilidad — rinden mejor AHORA que existe el eval-gate para medir la mejora post-DSPy vs baseline.

Fast-follows fichados (NO bloquean; detalle en la memoria): **(top) crecer el golden set con más casos `cited_answer`** — la señal RAG del gate es frágil por N=1 (una métrica = un booleano; un flip del juez = falsa regresión); `is_select` acepta CTEs escribibles (backstop txn readonly); `_canon` `str(v)` colisiones Decimal/None; higiene de `test_vectorstore` (no wipear el Qdrant compartido); test del None-guard de `_judge_yes`; endurecer juez intención↔SQL; + los previos de Fase 1 (UTC en reschedule/cancel, `_FIELD_LABELS` dup, `appt_resolve_limit`, denylist SQL, golden create↔cancel↔reschedule).
```

---

## Contexto de referencia (para vos / la próxima sesión)

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
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q       # 299 passed (docker arriba)
cd backend && .venv\Scripts\python -m app.eval.run                         # EVAL GATE (Ollama+PG+Qdrant+seed); --update-baseline p/ re-fijar
backend\.venv\Scripts\python -m pytest backend/tests -m eval -q            # 3 passed (wrapper del gate + smoke métricas + self-heal)
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
ollama list                                                                # gemma4:12b y gemma4:e4b
```
> Smoke navegador: Ollama + docker + schema/seed + spaCy `es_core_news_md`. **OJO: la suite fast (`-m "not llm"`) wipea el Qdrant compartido → re-sembrá (`seed_demo.py`) antes del smoke con RAG.** El front de Praxia va en `:3100` (`:3000` = app de vinos). Checklist en `frontend/SMOKE.md`.
