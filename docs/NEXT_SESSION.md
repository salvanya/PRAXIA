# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño). Respetá: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id`, escrituras solo por tools con confirmación humana (HITL), commits LIMPIOS sin atribución a Claude. OJO: `CLAUDE.md` está GITIGNORED (setup en `backend/.env.example`). mypy: el gate `mypy app/` está VERDE (pineado `1.13.*`; no metas ints ≥ 2^64 en `app/` — orjson en la cache).

2. **FASE 1 (MVP conversacional) CERRADA** — 10 slices en `origin`. **FASE 2 EN CURSO: Slice 1 (eval offline como GATE, `e4bb30f`), Slice 2 (MEMORIA LP + reflexión + Context Manager MÍNIMO, `5b328b5`) y Slice 3 (CONTEXT MANAGER COMPLETO, merge `a050b53`, 2026-07-06) CERRADOS Y PUSHEADOS a `origin`.** NO reabras nada de eso; el detalle vive en la memoria del proyecto.
   - SLICE 13 (Fase 2 #3) — Context Manager COMPLETO: `running_summary` incremental **post-turno** (best-effort + time-boxed, e4b, solo con desalojo; cap de banda `summary_max_fold_messages=20` lossless con catch-up) + **presupuesto de tokens** (heurística `estimate_tokens`≈chars/4) + **ensamblado prefijo-estable** en `app/context.py::build_chat_messages` (orden system→summary→memories→history; recorte historial-viejo→memorias→truncar el turno actual). `reflect_node` → **`consolidate_node`** (reflect + summary CONCURRENTES vía `asyncio.gather`, best-effort; wall-clock ≤10s). Nuevos campos `AgentState.running_summary`/`summarized_count`; nuevo `app/memory/summarize.py`. **Enfocado al camino conversacional (chitchat)**; sql/rag siguen single-turn (intactos). Config `context_token_budget=3000`/`summary_enabled`/`summary_timeout_s=8`/`summary_max_words=150`/`summary_max_fold_messages=20`. Smoke navegador VALIDADO (continuidad cross-ventana: recordó un dato del turno 1 varios turnos después; HITL intacto).

Estado (2026-07-06, main PUSHEADO a `origin`):
- Gates: `-m "not llm"` **343** (docker PG/Qdrant) · `-m eval` PASS (4 casos, 5 métricas 1.000, sin regresión) · `-m llm` (incl. e2e continuidad cross-ventana + 2 e2e memoria cross-thread) · `-m pii` 5 · frontend 35. **mypy `app/` Success (54 files)**.

Gotchas vigentes (detalle en la memoria del proyecto):
- **✅ `mypy app/` VERDE** (pineado `1.13.*`; el crash histórico `Integer exceeds 64-bit range` en write_cache no reproduce; no metas ints ≥ 2^64 en `app/`).
- **Lección de proceso:** cambios de firma/rename CROSS-CUTTING → correr la SUITE COMPLETA `-m "not llm"`, no solo los test files tocados.
- **500 en el navegador ≠ bug del grafo:** el front (`:3100`) proxya `/api/*`→`:8000`; si `dev.py` NO está arriba, Next devuelve 500. Verificá `netstat -ano | grep :8000` / `/health` antes de culpar al código. Esperá `Application startup complete` antes de tocar el chat.
- **Latencia de cierre de turno:** `reflect`+`summary` corren CONCURRENTES en `consolidate` (gather, ≤10s) pero in-graph → el `done` SSE se retrasa por turno de contenido. Fast-follow: background/detached.
- (previos) structured-output e4b None INTERMITENTE → text-parse/retry (args tipados 12b SÍ); imports nuevos en tests EXISTENTES al TOP (E402); `ruff format` ANTES de `ruff check`; backend con `dev.py` (no uvicorn directo, ProactorEventLoop); input plano a thread pausado en `confirm_action` descarta el interrupt; `test_vectorstore` wipea el Qdrant compartido bajo `-m "not llm"` (re-sembrá antes del smoke con RAG); front en `:3100` (`:3000` = app de vinos).

Tarea: continuar FASE 2 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea + whole-branch. No construyas de más (CLAUDE.md §7); Fase 3/4 = fuera de alcance.

Alcance restante de Fase 2 (orden acordado): **memoria RICA: update/delete/contradicción** (EL PRÓXIMO — decisión usuario 2026-07-03) → **DSPy** (MIPROv2/GEPA contra el golden set) · **caching** (semántico + embeddings) · **trazas Phoenix** · **guardrails ENDURECIDOS** (inyección con llm-guard, output safety, audit log — blueprint línea 556; el seam `G_OUT` va antes de `consolidate`).

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código):
- **MEMORIA RICA: update/delete/contradicción (EL PRÓXIMO, recomendado):** hoy NO se puede pisar/borrar una memoria (dedup solo casi-idéntico ≥0.9; las contradictorias conviven y el recall inyecta ambas). Enfoques: recall pesado por recencia/`salience` + chequeo de contradicción en `reflect` (UPDATE/supersede en vez de insertar) + comando explícito "olvidá/corregí que…" + UI de gestión / tighten del gate + **scope client/user** (hoy el gate rechaza datos de un cliente por PII → p.ej. "mi profesional de cabecera es X" no se guarda; lo destapó el smoke del Slice 13). Tabla ya prepped (`id`/`salience`/`last_used_at`/`source`; cols `client_id`/`user_id` presentes, sin escribir).
- Después: DSPy · caching · Phoenix · GUARDRAILS ENDURECIDOS (`G_OUT` antes de `consolidate`).

Fast-follows fichados (NO bloquean; detalle en la memoria):
- **(subió de prioridad) inyectar contexto/memoria en `propose_action`:** el smoke del Slice 13 destapó que "agendá un turno con mi profesional de cabecera" NO resuelve — las write-tools son single-turn y no ven memoria/summary/historial. Se cruza con memoria RICA (client-scope + identidad/auth Fase 4).
- background/detached de reflect+summary (latencia de cierre de turno).
- minors del review de Slice 13: budget arithmetic drift (overshoot marcador `…[truncado]` / undershoot overhead — dwarfed por headroom); edge tests `build_chat_messages(history=[])` y `budget=0` (código OK, sin cobertura); `_to_role_text` etiqueta no-`HumanMessage` como `"ai"` (latente); `touch_last_used` marca memorias que el presupuesto quizá dropea; golden case de VERBALIZACIÓN de continuidad post-DSPy (el 12b a veces no verbaliza el summary; e2e endurecido a la aserción del mecanismo).
- (previos) orphan PG si el timeout cancela mid-`store`; `_wipe` del e2e no limpia Qdrant; `long_term.recall` `.get("content")`; `test_build_wiring` edge-asserts; helpers `_run`/`_one_node_graph` a `conftest.py`; **Task 11 = caso de memoria en el eval-gate**; crecer golden `cited_answer`; endurecer juez intención↔SQL; `when`/`new_start_at`→`astimezone(UTC)`; `_FIELD_LABELS` dup; `appt_resolve_limit`; denylist SQL; golden create↔cancel↔reschedule.
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 13 — Context Manager COMPLETO (running_summary + presupuesto de tokens + prefijo estable) (2026-07-06)
- **Mergeado (`a050b53`, `--no-ff`) y PUSHEADO a `origin`.** Rama `fase2/context-manager` borrada. 11 commits (spec + plan + 8 tasks + fix del e2e + fix-wave de fast-follows). Completa el "Context Manager MÍNIMO" del Slice 12.
- **Diseño (enfocado a chitchat):** `app/context.py` es el builder único del camino conversacional. `estimate_tokens(text)` (heurística ≈ ceil(len/4), swappable), `format_summary_block(summary)` (system block, framing anti-inyección), `build_chat_messages(*, system, summary, memories, history, budget)` (**pura**: ensambla `[system, summary?, memories?, *history]` estable→volátil y recorta al presupuesto — dropea historial viejo → dropea bloque de memorias → trunca el turno actual con `…[truncado]`; inviolables system/summary/turno-actual). `app/memory/summarize.py::run(old_summary, new_messages, *, llm=None)` (e4b **texto plano** `.ainvoke`→`.content`, NO structured-output; retry ≤2x ante None; cap `summary_max_words`). Grafo: `reflect_node` → **`consolidate_node`** (rename; `asyncio.gather(_reflect_delta, _summary_delta, return_exceptions=True)`; solo el summary aporta delta de state). `_summary_delta`: dispara solo con desalojo (`evict_upto = len(msgs) - short_term_history_window > summarized_count`), **cap `fold_to = min(evict_upto, already + summary_max_fold_messages)`** (bounded + lossless: catch-up en chunks; avanza `summarized_count` a `fold_to`), best-effort + `wait_for(summary_timeout_s=8s)`, warning si el summary vuelve vacío. **INVARIANTE:** `summarized_count` NUNCA avanza sin summary guardado (el historial no se pierde en silencio). `chitchat_node` consume el builder; sql/rag/propose INTACTOS.
- **Estado/config nuevos:** `AgentState.running_summary: str` + `summarized_count: int` (campos PLANOS = replace, sin reducer). Config `context_token_budget=3000` (< num_ctx ~4096), `summary_enabled=True`, `summary_timeout_s=8.0` (≤ `memory_reflect_timeout_s`=10), `summary_max_words=150`, `summary_max_fold_messages=20`. `make_llm` NO fija `num_ctx` (fast-follow de hardening).
- **Reviews:** 8 task-reviews Approved (haiku/sonnet) + whole-branch opus **Ready=YES, 0 Critical** (best-effort airtight, invariante verificado, concurrencia share-free, rename limpio con HITL intacto). 2 Important NON-BLOCKING = fast-follows APLICADOS en el mismo slice (cap de banda + observabilidad, `c598c1b`). Gate `-m "not llm"` **343**, eval PASS (sin regresión: casos single-turn → summary no-op), e2e-llm de continuidad PASS. Spec/plan en `docs/superpowers/{specs,plans}/2026-07-0{4,5}-context-manager-completo*`. Ledger SDD en `.superpowers/sdd/progress.md` (gitignored).
- **Decisión de test (adjudicada en review):** el e2e de continuidad tiene aserción PRIMARIA dura sobre `running_summary` (contiene el hecho desalojado) + secundaria relajada (el 12b a veces NO verbaliza el contexto por el "no inventes datos" — variance de prompt-following, fast-follow DSPy). La continuidad queda cubierta determinísticamente por la primaria + el unit `test_chitchat_includes_running_summary`.
- **Smoke navegador VALIDADO (2026-07-06):** chitchat corto OK; **continuidad**: dicho un dato en el turno 1, tras varios turnos preguntó y lo RECORDÓ (running_summary); HITL abre la ConfirmCard con nombres explícitos (Confirmar escribe). **Hallazgo del smoke:** "agendá con mi profesional de cabecera" NO resuelve porque las write-tools son single-turn (no ven memoria/summary) → fast-follow "contexto en propose_action" subió de prioridad. **Gotcha operativo confirmado:** un 500 en el chat era `dev.py` caído (Next proxya `:3100`→`:8000`; upstream down = 500), NO un bug del grafo.

### Cierre Slice 12 — Memoria de largo plazo + reflexión + Context Manager (MÍNIMO) (2026-07-03)
- **Mergeado (`5b328b5`, `--no-ff`) y PUSHEADO a `origin`.** Rama `fase2/memoria-lp` borrada. 14 commits (spec + plan + 10 tasks + fix T5 + fix de regresión). Arregla el pain del Slice 8: memoria "general" disponible en TODOS los caminos, no solo chitchat.
- **Diseño:** grafo `router → recall → {rag|sql|chitchat|action|scope_reject} → reflect → END` (`scope_reject → END` directo, salta reflect; el `interrupt` HITL sigue dentro de `confirm_action`, intacto). **Store/recall** scope `practice`: Postgres `memories` (fuente de verdad, +col `source∈{reflexion,explicito}`) + Qdrant `praxia_memories` (1024/COSINE, calca `praxia_chunks`, `content` en payload → recall sin join). `app/memory/long_term.py` (`store` PG-first→Qdrant con compensación; dedup coseno ≥0.9→None; `recall` filtro `practice_id`+`scope='practice'`, piso 0.5, top_k 5; `touch_last_used`; `ensure_memories_collection`). **Reflexión** `app/memory/reflect.py` (gate e4b sí/no+`is_explicit` sesgo-a-False → extract e4b cap 3 → dedup → store; best-effort + `asyncio.wait_for(10s)`, NUNCA rompe el turno; e4b None→retry ≤2x; pydantic sin underscore). **Inyección** `app/context.py::format_memories_block` (system message tras el prompt estable, framing anti-inyección) en chitchat + `sql_present` (síntesis, NO el SELECT) + síntesis RAG (threading por `RagState`). Nodos `app/graph/memory_nodes.py`. `AgentState.memories`. `main.py` bootstrapea la colección. Config `ollama_model_cheap="gemma4:e4b"` + 8 flags `memory_*`.
- **Reviews:** 10 task-reviews Approved + whole-branch opus (Ready=With fixes → 1 fix T5 `recall_node` nested-try) + regresión del gate final (`test_graph.py::_fake_synth` sin `memories=`) cazada+fija (`04bad7c`). Gate `-m "not llm"` **323 passed / 0 failed**, e2e-llm **2** (cross-thread + aislamiento). Spec/plan en `docs/superpowers/{specs,plans}/2026-07-03-memoria-lp-context-manager*`.

### Cierre Slice 11 — Suite de eval offline como GATE (FASE 2 arranca) (2026-07-02)
- **Mergeado (`e4bb30f`, `--no-ff`) y PUSHEADO a `origin`.** Rama `fase-2/slice-eval-offline-gate` borrada. 14 commits (spec + plan + pivot-docs + 8 tasks + 3 fix-waves post-review). **Smoke navegador VALIDADO (2026-07-02).**
- **Diseño:** el gate corre el golden set (`backend/app/eval/golden_set.jsonl`) end-to-end por el grafo real y decide pass/fail = aserciones deterministas por-caso [DURO] + 4 métricas por LLM-as-judge con baseline-diff [REGRESIÓN]. Módulos `app/eval/{cases,checks,baseline,harness,metrics,run,fixtures}.py` + `baseline.json` (committeado, 5 métricas=1.0) + `README.md`. Marker `eval`; tests Ollama/PG = `eval`+`llm` (fuera de `-m "not llm"`).
- **PIVOT (decisión del usuario):** Ragas resultó dependency-incompatible con el stack congelado de Fase 1 → **jueces LLM-as-judge locales** (`gemma4:12b`). Cero deps nuevas.
- **Reliability fix (systematic-debugging):** `test_vectorstore.py` hace `delete_collection` y corre bajo `-m "not llm"` → la suite fast destruía el corpus RAG del gate. Fix: el gate auto-siembra su fixture (`fixtures.py`). `seed_demo.py` ingesta un `protocolo` demo.
- Spec/plan en `docs/superpowers/{specs,plans}/2026-07-02-eval-offline-gate*`.

### Cierres previos (Fase 1 CERRADA — detalle en la memoria del proyecto y en `docs/superpowers/`)
10 slices: grafo+router (`ae46438`) · CRAG (`d765eca`) · NL2SQL read-only (`8804a73`) · create_appointment HITL (`8e0ccfd`) · log_interaction+registry (`5138844`) · cancel (`f3d520b`) · reschedule+update_client (`494a511`) · memoria corto+slot-filling (`1739713`) · guardrails PII (`6004277`/`31f124f`) · canvas rico inline (`171bffd`). Todos mergeados+pusheados, smoke navegador validado.

### Comandos útiles
```bash
docker compose up -d                                                       # postgres + qdrant
backend\.venv\Scripts\python backend\seed_demo.py                          # 3 prof, 30 clientes, 80 turnos + doc protocolo
backend\.venv\Scripts\python backend\dev.py                                # backend :8000 (NO uvicorn directo; esperá "Application startup complete")
npm --prefix frontend run dev -- --port 3100                               # front en :3100 (:3000 lo ocupa el proyecto de vinos)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q       # 343 passed (docker arriba)
cd backend && .venv\Scripts\python -m app.eval.run                         # EVAL GATE (Ollama+PG+Qdrant+seed); --update-baseline p/ re-fijar
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q             # incl. e2e continuidad cross-ventana + 2 e2e memoria (requiere Ollama)
cd backend && .venv\Scripts\python -m mypy app/                            # VERDE: Success, 54 files (pineado 1.13.*)
ollama list                                                                # gemma4:12b y gemma4:e4b
```
> Smoke navegador: Ollama + docker + schema/seed + spaCy `es_core_news_md`. **OJO: la suite fast (`-m "not llm"`) wipea el Qdrant compartido → re-sembrá (`seed_demo.py`) antes del smoke con RAG.** El front de Praxia va en `:3100` (`:3000` = app de vinos). **Si el chat da 500, chequeá que `dev.py` esté arriba en `:8000` (`/health`) — el proxy de Next da 500 si el upstream está caído.** **Smoke de continuidad (Slice 13):** contá un dato al principio, charlá >5 turnos (para pasar la ventana de 10 mensajes), y preguntalo después → el `running_summary` debe influir. **Smoke de memoria (Slice 12):** decí "acordate que los turnos duran 30 minutos", reiniciá el thread, y preguntá algo relacionado → la memoria debe influir; las escrituras siguen abriendo la ConfirmCard (HITL intacto).
