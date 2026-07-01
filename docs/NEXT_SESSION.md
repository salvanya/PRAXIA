# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana (HITL), y commits LIMPIOS sin ninguna atribución a Claude. OJO: `CLAUDE.md` está GITIGNORED en este repo (las docs de setup compartidas viven en `backend/.env.example`).
2. Estamos en FASE 1 (MVP conversacional, alcance en CLAUDE.md §7). Ya están MERGEADOS a `main` y validados NUEVE slices — NO los reabras:
   - SLICE 1: grafo LangGraph + router semántico (merge ae46438).
   - SLICE 2: CRAG correctivo (d765eca): retrieve → rerank (bge-reranker-v2-m3) → juez relevancia → reformular → síntesis con citas → juez groundedness. Fuentes SOLO en grounded.
   - SLICE 3: Data Agent NL2SQL read-only (8804a73): NL→SELECT + capa semántica + validación sqlglot (practice_id AND-conjunct) + juez intención↔SQL + ejecutor read-only. Gotcha: gen SQL por TEXTO PLANO + sqlglot (with_structured_output da None).
   - SLICE 4: create_appointment HITL (8e0ccfd): 2 nodos propose(checkpointea)+confirm(interrupt→Command(resume)). SSE `confirm` + POST /chat/resume + ConfirmCard.
   - SLICE 5: log_interaction + REGISTRY de write-tools (5138844): nodos genéricos propose_action/confirm_action despachan por proposed_action["kind"]. resolve_single_client compartido. ConfirmCard agnóstico al kind.
   - SLICE 6: cancel_appointment 1ª MUTACIÓN (f3d520b): resolve_single_appointment fail-closed; db.cancel_appointment guard practice_id+status (None→idempotencia/TOCTOU).
   - SLICE 7: reschedule + update_client 2ª MUTACIÓN (494a511): reschedule reusa resolve_single_appointment; update_client COALESCE estructurado (phone/email/status/dob).
   - SLICE 8: memoria corto plazo (thread_id estable) + slot-filling cliente/turno (1739713): checkpointer persiste multi-turno; Clarification + resolvers.candidates + pending_clarification + choice_agent.resolve_choice (12b índice fail-closed→0) + clarify_node + ENTRY CONDICIONAL START→clarify si pending else router; re-invoca REGISTRY[kind].propose con client_override/appointment_override, encadena cliente→turno.
   - SLICE 9: GUARDRAILS PII (Presidio) (merge 6004277 + follow-up WYSIWYG 31f124f, 2026-07-01). Módulo `guardrails/pii.py` (Presidio + spaCy es_core_news_md + recognizers AR custom DNI/CUIT/PHONE; imports de presidio LAZY dentro de _engines() → import pii NO requiere presidio; API sync analyze/summarize/redact + PiiUnavailable; config PII_REDACTION_ENABLED/PII_SPACY_MODEL/PII_SCORE_THRESHOLD). INGESTA: tag PII NO-destructivo (documents.pii_summary JSONB, content INTACTO → RAG no degrada, fail-open). ESCRITURAS: WYSIWYG — log_interaction muestra y guarda el content CRUDO/verificable en la tarjeta (el follow-up REVIRTIÓ la redacción destructiva; pii.redact queda RESERVADO p/ audit/exports/docs de terceros = Fase 2, SIN caller en Fase 1). Notas → log_interaction type='nota'. update_client sin cambios (estructurado crudo). Spec/plan en docs/superpowers/{specs,plans}/2026-07-01-guardrails-pii* (el SPEC tiene un ADDENDUM con la reversión WYSIWYG = fuente de verdad del comportamiento final).

Estado y verificación (Slice 9 + follow-up WYSIWYG cerrados y MERGEADOS, 2026-07-01):
- Gate no-llm: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → 264 passed. `-m pii` → 5 (motor real Presidio+spaCy). `-m llm` → 20 (Ollama + ambos modelos + Postgres/Qdrant + modelo spaCy es_core_news_md). Lint: `ruff format` ANTES de `ruff check`; mypy con `--config-file backend/pyproject.toml`.
- Review final whole-branch (opus): ready-to-merge; cazó un BUG REAL (el teléfono AR "11-2233-4455" NO se redactaba porque el PhoneRecognizer built-in de Presidio no dispara en formatos locales AR → recognizer custom AR_PHONE). El follow-up WYSIWYG salió del smoke en navegador (redactar las propias notas clínicas es contraproducente).
- main PUSHEADO a origin (github.com/salvanya/PRAXIA), HEAD 31f124f. Infra/comandos abajo.

Gotchas vigentes:
- structured-output de e4b devuelve None INTERMITENTE → router Y classify_write_action usan ainvoke + text-parse (fail-closed: router→chitchat, classify→unsupported). Args tipados del 12b (with_structured_output) SÍ; resolve_choice 12b structured int SÍ. Regla: structured output OK para bool/enum/IDs; texto libre → plano + validación.
- Imports nuevos en archivos de tests EXISTENTES van al TOP (ruff E402). Archivos de test nuevos no tienen el problema.
- Tests del front: `npm --prefix frontend run test -- --run`.
- Lint: `ruff format` ANTES de `ruff check` (E501 exime `# type: ignore`).
- e2e -m llm con modelo local: hiccups de Ollama → fail-closed a abstención. Asserts no-vacuos + reintentá; NO debilites.
- (Slice 8, LangGraph) input de TEXTO PLANO a un thread pausado en confirm_action DESCARTA el interrupt y RE-EJECUTA desde el entry (NO escribe; proposed_action huérfano inerte) → tests de slot-filling mockean classify_intent.
- (Slice 9, f) CLAUDE.md GITIGNORED en este repo → NO se commitea; setup docs en backend/.env.example. Presidio: imports LAZY dentro de _engines(). El -m llm ahora requiere el modelo spaCy `es_core_news_md` (sin él, log_interaction seguiría funcionando —ya no redacta— pero los -m pii se saltan).
- (Slice 9, g) MUCHOS EDITS RÁPIDOS SEGUIDOS CUELGAN EL HOT-RELOAD de uvicorn (el log muestra "WatchFiles… Reloading..." SIN el "Application startup complete" que le sigue) → el worker viejo sirve código STALE. Fix: matar el árbol del proceso reloader (`taskkill //F //T //PID <pid>`, PID de `netstat -ano | grep :8000`) + relanzar `backend\.venv\Scripts\python backend\dev.py`.

Tarea: arrancar el PRÓXIMO SLICE de Fase 1 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7.

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código):
- CANVAS MÁS RICO (RECOMENDADO): es el ÚLTIMO ítem del MVP de Fase 1 — ya están grafo+router, CRAG, NL2SQL, 5 write-tools HITL, memoria corto+slot-filling y guardrails PII básicos. Migrar <Thread> de @assistant-ui/react a @assistant-ui/react-ui + render de tablas (resultados SQL) / fichas / citas (RAG) / tarjetas de confirmación más ricas. Con esto Fase 1 quedaría CERRADA. (En 0.7.91 <Thread> NO está deprecado; la migración trae Tailwind/CSS y es el alcance del canvas.)
- FASE 2 (si preferís profundizar backend): DSPy (MIPROv2/GEPA) + suite de eval offline como GATE + memoria largo plazo/reflexión + caching (semántico+embeddings) + trazas Phoenix + Context Manager (running_summary) + guardrails ENDURECIDOS (inyección de prompt, output safety, audit log completo — blueprint línea 556).

Fast-follows fichados (NO bloquean): ENDURECER el juez intención↔SQL (a veces aprueba un SELECT arbitrario — ej. "quién es mi profesional?" devuelve un profesional en vez de abstener); when/new_start_at→astimezone(UTC) en reschedule/cancel/action; consolidar _FIELD_LABELS (dup update_client_agent↔write_tools); appt_resolve_limit dedicado; golden create↔cancel↔reschedule; logging.warning si db.get_client→None; denylist SQL (pg_read_file/pg_sleep); audit log (agent_runs)+consents; created_by (auth real); timezone por práctica; proposed_action huérfano (higiene); route_after_clarify alias dead-code. NUEVOS de Slice 9: recognizers AR con score 0.4<0.5 → un DNI/teléfono SIN context word cerca ("dni"/"tel") no se redacta (relevante para cuando se use pii.redact en Fase 2); notas sin test behavioral de routing (solo prompt-grep + e2e log_interaction); _warned global de módulo; laziness-test débil (reload+hasattr).
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 9 — Guardrails PII (Presidio) + follow-up WYSIWYG (2026-07-01)
- **Mergeado (`6004277`, `--no-ff`) + follow-up WYSIWYG (`31f124f`, `--no-ff`), ambos PUSHEADOS a `origin`.** Ramas `fase-1/slice-guardrails-pii` y `fase-1/interaction-raw-content` borradas. Ejecución subagent-driven (8 tasks TDD + fix Task2 lazy-boundary + fix-wave final phone/CUIT; luego follow-up WYSIWYG directo). Modelos: haiku para transcripción trivial (T1,T5), sonnet para el resto, review final opus.
- **Módulo `guardrails/pii.py`:** Presidio `AnalyzerEngine`+`AnonymizerEngine` con NLP spaCy `es_core_news_md` + recognizers AR custom `AR_DNI`/`AR_CUIT`/`AR_PHONE` (regex `\b\d{2,4}-\d{3,4}-\d{4}\b`, no clobbea DNI-con-puntos). **Imports de presidio LAZY dentro de `_engines()`** (lru_cache) → `import app.guardrails.pii` nunca requiere presidio; solo `_engines()` lanza `PiiUnavailable`. API sync `analyze`/`summarize`/`redact` (los callers async las envuelven en `asyncio.to_thread`). Config `PII_REDACTION_ENABLED`/`PII_SPACY_MODEL`/`PII_SCORE_THRESHOLD`.
- **Ingesta (NO-destructivo, se mantiene):** `ingest_document` calcula `pii.summarize(full_text)` → guarda el conteo en `documents.pii_summary` (JSONB, columna nueva, `set_document_status(pii_summary=)` con `json.dumps`+`$5::jsonb`+COALESCE, sin codec asyncpg; `db.get_document` con `json.loads`). El content que va a Qdrant queda INTACTO → RAG no degrada. **Fail-open** (`_safe_pii_summary` catch-all → None). *(Probado: `ficha_paciente_demo.md` → `{PERSON:4, AR_DNI:2, AR_PHONE:2, AR_CUIT:1, EMAIL_ADDRESS:1, LOCATION:3}`.)*
- **Escrituras: WYSIWYG (el follow-up REVIRTIÓ la redacción destructiva).** `log_interaction` muestra y guarda el `content`/`summary` **CRUDOS**; la tarjeta muestra el `content` real para verificar antes de confirmar (`propose_interaction` ya NO llama a `pii.redact`; `_card_summary` usa el content). La **Decisión #3 del spec queda ANULADA** (addendum en el spec). `pii.redact` queda en el módulo **sin caller en Fase 1**, reservado para superficies no confiables/compartidas (audit log/exports/docs de terceros) = **Fase 2**. `update_client` sin cambios (estructurado crudo/verificable). Motivo del revert: *no se puede confirmar a ciegas* — redactar las propias notas clínicas del profesional destruye su valor (fricción anticipada en el brainstorming).
- **Notas habilitadas** vía `log_interaction type='nota'` (CLASSIFY_PROMPT: "nota" sale de unsupported → log_interaction). `clients.notes` (campo blob) descartado a favor de interactions (fechado/queryable) + `clients.tags JSONB` para atributos.
- **Gate final:** no-llm **264**, `-m pii` **5** (motor real), `-m llm` **20**, ruff/mypy limpio, 0 warnings. Review final whole-branch (opus) *ready-to-merge*; cazó un BUG REAL (teléfono AR no se redactaba → `AR_PHONE`).
- **Decisiones del usuario:** (a) semántica HÍBRIDA (ingesta no-destructiva / escrituras destructivas) en el brainstorming; (b) tras el smoke, **REVERT a WYSIWYG** en escrituras (mostrar/guardar crudo). El `-m llm` ahora requiere el modelo spaCy.
- **Gotchas nuevos:** (f) `CLAUDE.md` GITIGNORED → setup docs en `.env.example`. (g) muchos edits rápidos cuelgan el hot-reload de uvicorn → matar el árbol del reloader + relanzar `dev.py`.

### Cierre Slice 8 — memoria corto plazo (thread_id estable) + slot-filling (2026-06-30)
- **Mergeado (`1739713`, `--no-ff`) + PUSHEADO (`10ae6e2`).** thread_id estable end-to-end (front `useRef` + backend `select_chat_input` incremental/inicial vía `aget_state`, no pisa el checkpoint) → checkpointer Postgres persiste multi-turno; `chitchat` ve historial (`short_term_history_window=10`). Slot-filling (re-invocar propose con overrides): `Clarification` + `pending_clarification` + `choice_agent.resolve_choice` (12b, fail-closed→0) + `clarify_node` + entry condicional; encadena cliente→turno. Las 5 write-tools aceptan ambos overrides. Gate no-llm 244, -m llm 19/19. Review opus *Ready YES* (HITL airtight by construction). Decisión del usuario: ROUTER_PROMPT +2 líneas (chitchat reconoce meta-preguntas).

### Cierres previos (condensados — detalle en los specs/plans y en la memoria del proyecto)
- **Slice 7 — reschedule + update_client** (`494a511`): reschedule reusa resolve_single_appointment; update_client COALESCE estructurado SIN notes. no-llm 206.
- **Slice 6 — cancel_appointment** (1ª mutación, `f3d520b`): resolve_single_appointment fail-closed; db.cancel_appointment guard practice_id+status. no-llm 178.
- **Slice 5 — log_interaction + REGISTRY** (`5138844`): REGISTRY dict[str, WriteTool], nodos genéricos por kind, resolve_single_client compartido. no-llm 157.
- **Slice 4 — create_appointment HITL** (`8e0ccfd`): 2 nodos (propose checkpointea + confirm interrupt/resume), SSE confirm + /chat/resume, ConfirmCard. 1ª publicación a origin.
- **Slice 3 — NL2SQL read-only** (`8804a73`): capa semántica + validación sqlglot (practice_id AND-conjunct) + juez intención↔SQL + ejecutor read-only.
- **Slice 2 — CRAG** (`d765eca`): retrieve→rerank→juez relevancia→reformular→síntesis con citas→juez groundedness; fuentes solo en grounded.
- **Slice 1 — grafo + router** (`ae46438`): máquina de estados LangGraph, router e4b, checkpointer AsyncPostgresSaver.
- **Fase 0** (aceptada 2026-06-25): docker-compose, schema, ingesta (parse→chunk→embed→Qdrant), datos sintéticos.

### Comandos útiles
```bash
docker compose up -d
backend\.venv\Scripts\python backend\seed_demo.py                          # 3 prof, 30 clientes, 80 turnos
backend\.venv\Scripts\python backend\dev.py                                # backend :8000 (NO uvicorn)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q       # 264 passed
backend\.venv\Scripts\python -m pytest backend/tests -m pii -q             # 5 passed (Presidio + spaCy es_core_news_md)
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q             # 20 passed (requiere Ollama + spaCy)
backend\.venv\Scripts\python -m spacy download es_core_news_md             # guardrails PII (una vez)
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
npm --prefix frontend run dev                                              # front :3000/3001/3002
npm --prefix frontend run test -- --run; npm --prefix frontend run lint; npm --prefix frontend run build
ollama list                                                                # gemma4:12b y gemma4:e4b
```
> Para el smoke LLM real: Ollama corriendo + `docker compose up -d` + schema/seed aplicados + modelo spaCy bajado. Smoke manual del navegador en `frontend/SMOKE.md`. Gotcha: si el hot-reload de uvicorn se cuelga tras muchos edits, matá el reloader (`taskkill //F //T //PID <pid de netstat :8000>`) y relanzá `dev.py`.
