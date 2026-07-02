# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana (HITL), y commits LIMPIOS sin ninguna atribución a Claude. OJO: `CLAUDE.md` está GITIGNORED en este repo (las docs de setup compartidas viven en `backend/.env.example`).

2. **FASE 1 (MVP conversacional) está CERRADA** — DIEZ slices mergeados a `main` y pusheados a `origin`, smoke navegador validado en todos. NO los reabras:
   - SLICE 1: grafo LangGraph + router semántico (ae46438).
   - SLICE 2: CRAG correctivo (d765eca): retrieve → rerank → juez relevancia → reformular → síntesis con citas → juez groundedness.
   - SLICE 3: Data Agent NL2SQL read-only (8804a73): NL→SELECT + capa semántica + sqlglot (practice_id AND-conjunct) + juez intención↔SQL. Gotcha: gen SQL por TEXTO PLANO (structured da None).
   - SLICE 4: create_appointment HITL (8e0ccfd): 2 nodos propose(checkpointea)+confirm(interrupt→resume).
   - SLICE 5: log_interaction + REGISTRY de write-tools (5138844): nodos genéricos por proposed_action["kind"].
   - SLICE 6: cancel_appointment 1ª MUTACIÓN (f3d520b).
   - SLICE 7: reschedule + update_client 2ª MUTACIÓN (494a511).
   - SLICE 8: memoria corto plazo (thread_id estable) + slot-filling cliente/turno (1739713).
   - SLICE 9: guardrails PII (Presidio) + WYSIWYG (6004277 / 31f124f): pii.py (Presidio + spaCy es_core_news_md + recognizers AR, imports LAZY). Ingesta = tag no-destructivo (documents.pii_summary); escrituras = WYSIWYG (content crudo; pii.redact reservado a Fase 2, sin caller).
   - SLICE 10: canvas más rico — artefactos inline (171bffd, 2026-07-02): tabla SQL real (evento SSE `table` nuevo), citas RAG y ConfirmCards por-kind, todo INLINE vía content-parts `tool-call` de assistant-ui 0.7.91 + Tool UIs; reducer puro `frontend/lib/messageParts.ts`; Tailwind v3 (CSS prearmado a-ui + `plugins:[]`); hedge `result`+`status:complete` p/ el render; HITL intacto. Detalle abajo.

Estado (2026-07-02, todo pusheado a origin, HEAD 171bffd):
- Gates: backend `-m "not llm"` 272 (docker Postgres/Qdrant), `-m llm` 20, `-m pii` 5; frontend 35 tests + lint + build. Lint: `ruff format` ANTES de `ruff check`; mypy `--config-file backend/pyproject.toml`.

Gotchas vigentes:
- (a) structured-output de e4b devuelve None INTERMITENTE → router Y classify_write_action usan ainvoke + text-parse (fail-closed). Args tipados del 12b SÍ; resolve_choice 12b int SÍ. Regla: structured OK bool/enum/IDs; texto libre → plano + validación.
- (b) imports nuevos en archivos de test EXISTENTES van al TOP (ruff E402); archivos nuevos no tienen el problema.
- (c) lint = `ruff format` ANTES de `ruff check` (E501 exime `# type: ignore`).
- (d) harness PowerShell puede correr con cwd=backend → exe por ruta relativa con `&`/`.\`.
- (e) input de TEXTO PLANO a un thread pausado en confirm_action DESCARTA el interrupt y RE-EJECUTA desde el entry (no escribe) → tests de slot-filling mockean classify_intent.
- (f) `CLAUDE.md` GITIGNORED → setup docs en `backend/.env.example`. Los `-m llm`/`-m pii` requieren el modelo spaCy `es_core_news_md`.
- (g) muchos edits rápidos seguidos CUELGAN el hot-reload de uvicorn ("Reloading..." sin "Application startup complete") → worker stale; matar el árbol del reloader (`taskkill //F //T //PID <pid de netstat :8000>`) + relanzar `dev.py`.
- (h, Slice 10/frontend) los artefactos ricos son content-parts `tool-call` renderizados por Tool UIs registrados en `<Thread tools={[...]}>` (`frontend/components/toolUIs.tsx`); los `toolName` (praxia_sources/praxia_sql_table/praxia_confirm) deben coincidir entre el reducer (`messageParts.ts`) y los Tool UIs. Cada tool-call lleva `result` + el yield final `status:{complete}` (hedge del render). Un subagente headless NO puede validar el render en navegador → el smoke del canvas es manual (`frontend/SMOKE.md` → "Canvas rico").

Tarea: arrancar FASE 2 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7. Fase 3/4 = fuera de alcance.

Alcance de Fase 2 (CLAUDE.md §7): DSPy (MIPROv2/GEPA, prompts compilados contra el golden set) · suite de eval offline como GATE (faithfulness, answer relevance, context precision/recall, execution accuracy del SQL; eval/golden_set.jsonl) · memoria largo plazo + reflexión (tabla `memories` + Qdrant, recuperación por coseno) · caching (semántico + embeddings) · trazas en Arize Phoenix · Context Manager (prefijo estable + resumen incremental `running_summary` + memorias relevantes en TODOS los caminos, no solo chitchat) · guardrails ENDURECIDOS (inyección de prompt con llm-guard, output safety, audit log completo — blueprint línea 556).

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código). Sugerencia de orden:
- SUITE DE EVAL OFFLINE COMO GATE (recomendado de arranque): habilita medir regresiones de retrieval/SQL/síntesis/router ANTES de tocar DSPy/memoria/caching; es el cimiento de confiabilidad de Fase 2 (CLAUDE.md §6 ya la referencia como gate). Golden set arranca chico y crece con cada bug.
- CONTEXT MANAGER + memoria largo plazo/reflexión: la memoria "general" (contexto en todos los caminos, no solo chitchat) — destapado como limitación del Slice 8.
- GUARDRAILS ENDURECIDOS (inyección de prompt / output safety / audit log): sube el piso de seguridad.
- DSPy + caching + Phoenix: optimización y observabilidad (mejor después del eval-gate para poder medir).

Fast-follows fichados (NO bloquean; detalle en la memoria del proyecto): endurecer el juez intención↔SQL (a veces aprueba un SELECT arbitrario, ej. "quién es mi profesional?" inventa uno); `when`/`new_start_at`→`astimezone(UTC)` en reschedule/cancel/action; consolidar `_FIELD_LABELS` (dup update_client_agent↔write_tools); `appt_resolve_limit` dedicado; golden create↔cancel↔reschedule; denylist SQL (pg_read_file/pg_sleep); audit log (agent_runs)+consents; created_by (auth real, Fase 4); timezone por práctica (Fase 4). De Slice 10: fichas de cliente (flujo backend propio); before→after en la card de update_client (agregar `before` a params + `default=str` en el evento confirm); citas clickeables/`doc_type`/visor de docs; sort/filter/export de tabla; markdown en prosa (`@assistant-ui/react-markdown`); artefactos NO persisten en history replay (SSE efímero — relevante cuando haya replay backend).
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 10 — Canvas más rico (FASE 1 CERRADA) (2026-07-02)
- **Mergeado (`171bffd`, `--no-ff`) y PUSHEADO a `origin`.** Rama `fase-1/slice-rich-canvas` borrada. 15 commits (spec + plan + 8 tasks + fixes del controller). Ejecución subagent-driven: 9 tasks TDD (haiku para transcripción, sonnet para integración) + 3 fix-waves del controller (hedge `result`; `SqlTable` sin fallback `Object.keys`; gaps de cobertura del review final) + review whole-branch opus. **Cierra el MVP de Fase 1.**
- **Mecanismo (assistant-ui 0.7.91):** cada artefacto es un content-part `tool-call` que el runtime adapter emite; un Tool UI registrado (`makeAssistantToolUI` + `<Thread tools={[...]}>`) lo renderiza. **NO se migró a `@assistant-ui/react-ui`** (no existe en 0.7.91 — `@assistant-ui/react` es el paquete actual). Reducer PURO `frontend/lib/messageParts.ts` (`reduceEvent` evento→artefactos, `toContent`→content-parts; texto primero, `toolCallId` estable por posición; hedge: cada tool-call lleva `result` + yield final `status:{complete}` para evitar `requires-action`).
- **3 artefactos:** Citations (`praxia_sources`, footnotes numeradas — reemplaza el markdown aplanado del `sourcesBlock`) · SqlTable (`praxia_sql_table`, tabla real read-only + toggle "ver consulta") · ConfirmCard por-kind inline (`praxia_confirm`, `cardFields()` pura: campos legibles por kind, IDs ocultos, cancel destructivo rojo; reemplaza el camino viejo `onConfirm`/`pending`, removido).
- **Backend (toque chico):** evento SSE `table` NUEVO (`nodes.write_table` + `sql_node` emite SOLO en caso tabular —no escalar/vacío/abstenido—; `main._sse_event_stream` reenvía con `default=str` por los tipos asyncpg) + `sql_present` dejó de embeber tabla markdown en la prosa (guard `_has_md_table`). **CERO cambios en HITL/RAG/router/`propose_*`.**
- **Estilos:** Tailwind v3 (variante SEGURA por ejecución headless: se mantuvo el CSS prearmado de a-ui + `plugins:[]`, componentes con clases estándar; NO se usó el preset a-ui) + i18n español (`welcome`/`strings`).
- **HITL intacto:** la card sigue llamando `resumeChat`→`/chat/resume`; el interrupt del backend NO se tocó.
- **Gate:** no-llm **272**, frontend **35** + lint + build; review whole-branch (opus) *Ready to merge YES, 0 Critical* (mecanismo trazado contra el source real de a-ui: `AssistantMessageContent` mapea `tools`→`by_name`→Tool UI; HITL/UTC/serialización/commits limpios verificados). **Smoke navegador VALIDADO (2026-07-02):** citas inline, tabla+toggle (escalar=solo frase), ConfirmCard por-kind con HITL, chitchat sin artefactos. Spec/plan en `docs/superpowers/{specs,plans}/2026-07-01-rich-canvas*`.

### Cierres previos (Fase 1, condensados — detalle en la memoria del proyecto y en `docs/superpowers/{specs,plans}/`)
- **Slice 9 — Guardrails PII (Presidio) + WYSIWYG** (`6004277` / `31f124f`): `guardrails/pii.py` (Presidio + spaCy `es_core_news_md` + recognizers AR DNI/CUIT/PHONE, imports LAZY dentro de `_engines()`; API sync + `PiiUnavailable`). Ingesta = tag NO-destructivo (`documents.pii_summary` JSONB, content intacto, fail-open). Escrituras = WYSIWYG (content CRUDO/verificable; `pii.redact` reservado a superficies no confiables = Fase 2, sin caller). Notas → `log_interaction type='nota'`.
- **Slice 8 — memoria corto plazo + slot-filling** (`1739713`): thread_id estable end-to-end (front `useRef` + backend `select_chat_input`); checkpointer Postgres persiste multi-turno; `Clarification` + `choice_agent.resolve_choice` + `clarify_node` + entry condicional; re-invoca propose con overrides, encadena cliente→turno. HITL airtight by construction.
- **Slice 7 — reschedule + update_client** (`494a511`, 2ª mutación) · **Slice 6 — cancel_appointment** (`f3d520b`, 1ª mutación) · **Slice 5 — log_interaction + REGISTRY** (`5138844`) · **Slice 4 — create_appointment HITL** (`8e0ccfd`, 2 nodos propose/confirm + interrupt) · **Slice 3 — NL2SQL read-only** (`8804a73`, capa semántica + sqlglot + juez intención↔SQL) · **Slice 2 — CRAG** (`d765eca`) · **Slice 1 — grafo + router** (`ae46438`) · **Fase 0** (docker-compose + schema + ingesta parse→chunk→embed→Qdrant + datos sintéticos).

### Comandos útiles
```bash
docker compose up -d
backend\.venv\Scripts\python backend\seed_demo.py                          # 3 prof, 30 clientes, 80 turnos
backend\.venv\Scripts\python backend\dev.py                                # backend :8000 (NO 'uvicorn app.main:app' directo)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q       # 272 passed
backend\.venv\Scripts\python -m pytest backend/tests -m pii -q             # 5 passed (Presidio + spaCy es_core_news_md)
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q             # 20 passed (Ollama + ambos modelos + PG/Qdrant + spaCy)
backend\.venv\Scripts\python -m spacy download es_core_news_md             # guardrails PII (una vez)
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
npm --prefix frontend run dev                                              # front :3000
npm --prefix frontend run test -- --run; npm --prefix frontend run lint; npm --prefix frontend run build
ollama list                                                                # gemma4:12b y gemma4:e4b
```
> Smoke navegador: Ollama + `docker compose up -d` + schema/seed + modelo spaCy bajado. Checklist en `frontend/SMOKE.md` (incl. sección **"Canvas rico"**: citas / tabla+toggle "ver consulta" / ConfirmCard por-kind con HITL / chitchat sin artefactos). Gotcha: si el hot-reload de uvicorn se cuelga tras muchos edits, matá el reloader (`taskkill //F //T //PID <pid de netstat :8000>`) y relanzá `dev.py`.
