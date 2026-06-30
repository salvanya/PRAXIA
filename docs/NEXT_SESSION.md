# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana (HITL), y commits LIMPIOS sin ninguna atribución a Claude.
2. Estamos en FASE 1 (MVP conversacional, alcance en CLAUDE.md §7). Ya están MERGEADOS a `main` y validados (tests + smoke navegador) OCHO slices — NO los reabras:
   - SLICE 1: grafo LangGraph + router semántico (merge ae46438).
   - SLICE 2: subgrafo CRAG correctivo (merge d765eca): retrieve → rerank (bge-reranker-v2-m3) → juez relevancia → reformular/reintentar → síntesis con citas → juez de groundedness → emitir/abstener. Fuentes SOLO en el camino grounded.
   - SLICE 3: Data Agent NL2SQL read-only (merge 8804a73): NL→SELECT con capa semántica → validación sqlglot → juez intención↔SQL → abstención fail-closed → ejecutor read-only. Gotcha: la gen SQL va por TEXTO PLANO + sqlglot (with_structured_output da None).
   - SLICE 4: write-tool create_appointment con HITL (merge 8e0ccfd): DOS nodos — propose (extrae args + resolver determinístico, se checkpointea) + confirm (interrupt → Command(resume) escribe/cancela). Transporte /chat stream_mode=["custom","updates"] + POST /chat/resume + ConfirmCard.
   - SLICE 5: log_interaction + REGISTRY de write-tools (merge 5138844): nodos genéricos propose_action/confirm_action despachan por proposed_action["kind"] vía REGISTRY (agents/write_tools.py). resolve_single_client compartido. ConfirmCard agnóstico al kind.
   - SLICE 6: cancel_appointment — 1ª MUTACIÓN (merge f3d520b): resolve_single_appointment (fail-closed). db.find_cancellable_appointments + db.cancel_appointment (guard practice_id+status, None→idempotencia/TOCTOU).
   - SLICE 7: reschedule_appointment + update_client — 2ª MUTACIÓN (merge 494a511): reschedule reusa resolve_single_appointment; update_client = COALESCE estructurado (phone/email/status/dob) SIN notes (diferido a Guardrails). REGISTRY/CLASSIFY_PROMPT 6 kinds.
   - SLICE 8: memoria de corto plazo (thread_id estable) + slot-filling de desambiguación (cliente y turno) (merge 1739713, 2026-06-30). thread_id estable end-to-end (front useRef + backend select_chat_input incremental/inicial vía aget_state, NO pisa el checkpoint) → checkpointer persiste multi-turno; chitchat ve historial reciente (short_term_history_window=10). Slot-filling (re-invocar propose con overrides): Clarification + resolvers.candidates + pending_clarification (state) + choice_agent.resolve_choice (12b índice fail-closed→0) + clarify_node + ENTRY CONDICIONAL START→clarify si pending else router; re-invoca REGISTRY[kind].propose(..., client_override/appointment_override), encadena cliente→turno. Las 5 write-tools + clarify_or_abstain_*; propose_appointment UNIFICADO sobre resolve_single_client. No-mapea → limpia pending + reintento. Spec/plan en docs/superpowers/{specs,plans}/2026-06-30-short-term-memory-slot-filling*.

Estado y verificación (Slice 8 cerrado y MERGEADO, 2026-06-30):
- Gate no-llm: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → 244 passed. `-m llm` → 19/19 (Ollama + ambos modelos + Postgres/Qdrant). Lint: `ruff format` ANTES de `ruff check`; mypy con `--config-file backend/pyproject.toml`.
- Review final whole-branch (opus): Ready to merge YES, sin Critical. HITL airtight by construction (invariante pending_clarification truthy ⟹ proposed_action None); multi-tenant scoped; fail-closed en capas. Fix-wave de 2 Important (no-clobber del estado en /chat con logger+503; test de pausa/mensaje) + systematic-debugging de 1 test.
- DECISIÓN del usuario ACEPTADA: ROUTER_PROMPT +2 líneas (chitchat reconoce meta-preguntas "¿qué te dije?") pese a "router no cambia" del spec — necesario p/ memoria conversacional end-to-end, anotado en el spec.
- Smoke §2 navegador VALIDADO (2026-06-30): slot-filling de turno (cancela el elegido, deja el otro) + de cliente (nombre ambiguo) + memoria conversacional (chitchat recuerda) + no-mapea (NO escribe) + one-shot/SQL/chitchat no-regresión; verificado en DB. LIMITACIÓN conocida (NO bug): "quién es mi profesional de referencia?" rutea a sql (no usa la memoria); la memoria conversacional vive en chitchat → el follow-up contextual de sql/rag es No-objetivo (queda para Fase 2: Context Manager + memoria largo plazo/reflexión).
- main PUSHEADO a origin (github.com/salvanya/PRAXIA). Infra: docker compose up -d. Backend: backend\.venv\Scripts\python backend\dev.py (NO uvicorn: ProactorEventLoop vs psycopg async; dev.py cwd-agnóstico via __file__). Frontend: npm --prefix frontend run dev (Next salta a 3000/3001/3002). Seed: backend\.venv\Scripts\python backend\seed_demo.py (3 prof, 30 clientes, 80 turnos, determinista). Harness PowerShell: cwd=backend, exe relativo con `&`/`.\`; backend bindea 127.0.0.1.

Gotchas vigentes:
- structured-output de e4b devuelve None INTERMITENTE → router Y classify_write_action usan ainvoke + text-parse (fallback fail-closed: router→chitchat, classify→unsupported). Los args tipados del extractor 12b (with_structured_output) SÍ funcionan; resolve_choice usa 12b structured int (confiable). Regla: structured output OK para bool/enum/IDs; texto libre → plano + validación.
- Imports nuevos en archivos de tests EXISTENTES van al TOP (ruff E402, select=["E",…]). Archivos de test nuevos no tienen el problema.
- Tests del front: `npm --prefix frontend run test -- --run` (NO `npx --prefix frontend vitest run`).
- Lint: `ruff format` ANTES de `ruff check` (E501 marca código plano largo pero exime `# type: ignore`).
- e2e -m llm con modelo local: hiccups de Ollama → fail-closed a abstención. Asserts no-vacuos + reintentá; NO debilites.
- NUEVO (Slice 8, gotcha de LangGraph): un input de TEXTO PLANO a un thread pausado en confirm_action (interrupt) DESCARTA el interrupt y RE-EJECUTA desde el entry (NO re-interrumpe; NO escribe; proposed_action queda huérfano INERTE — confirm solo se alcanza vía propose/clarify que lo recomputan). Los tests de slot-filling (test_slotfill_cycle.py) DEBEN mockear classify_intent (router) para ser deterministas/no-llm.

Tarea: arrancar el PRÓXIMO SLICE de Fase 1 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7.

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código):
- GUARDRAILS (RECOMENDADO): Presidio PII español en la entrada del grafo + redacción del content de log_interaction (hoy SIN redacción) + habilita update_client.notes (texto libre, diferido a Guardrails). Sirve la directiva §0 (datos de salud); con la memoria de corto plazo del Slice 8 el checkpointer ahora PERSISTE PII conversacional → MÁS urgente. OJO de alcance a confirmar: el blueprint pone PII redaction en Fase 1 (§6 línea 545) pero detección de INYECCIÓN en Fase 2 (línea 556, "guardrails endurecidos"); scope_reject/structured/SQL-read-only ya existen.
- Canvas más rico: migrar <Thread> a @assistant-ui/react-ui + tablas/fichas/citas/tarjetas.
- (Memoria de corto plazo + slot-filling YA se hicieron en Slice 8. La memoria "general" independiente del ruteo = Fase 2: Context Manager + running_summary + memoria largo plazo/reflexión.)

Fast-follows fichados (NO bloquean): when/new_start_at→astimezone(UTC) en reschedule/cancel/action; consolidar _FIELD_LABELS (duplicado update_client_agent ↔ write_tools); appt_resolve_limit dedicado; golden create↔cancel↔reschedule; logging.warning si db.get_client→None; denylist SQL (pg_read_file/pg_sleep); audit log (agent_runs)+consents; created_by (auth real); timezone por práctica. NUEVOS de Slice 8: ENDURECER el juez intención↔SQL (a veces aprueba un SELECT arbitrario para preguntas sin respuesta de datos — ej. "quién es mi profesional?" devuelve un profesional en vez de abstener); proposed_action huérfano tras descartar interrupt por mensaje plano (inerte, higiene); resolve_choice/_history_messages usan ollama_model (=12b por default; coupling latente si se apunta a e4b); _handle_proposal_result happy-path omite sources:[] (inocuo); route_after_clarify alias dead-code. (El resolver de cliente de propose_appointment YA se unificó en Slice 8.)

Ítems DIFERIDOS a Fase 1: canvas rico (tablas/fichas/citas + migración @assistant-ui/react-ui); botón "Editar" en la tarjeta; copy de botones de ConfirmCard para cancelaciones ("Sí, cancelar"/"No"); afinar el router con DSPy (Fase 2) — caso límite: "¿atienden los domingos?" rutea a sql en vez de rag.
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 8 — memoria corto plazo (thread_id estable) + slot-filling (cliente y turno) (2026-06-30)
- **Mergeado (`1739713`, `--no-ff`) a `main` y PUSHEADO a `origin`.** 16 commits (2 docs spec/plan + 11 impl + fix-wave + fix de test + anotación spec), autoría limpia. Rama `fase-1/slice-8-short-term-memory-slot-filling` borrada. Ejecución subagent-driven (11 tasks TDD + 1 fix-wave de 2 Important + 1 systematic-debugging). Modelos: haiku para transcripción (T1,3,5,6), sonnet para integración/front/UNCHANGED (T2,4,7,8,9,10,11), reviewers sonnet, review final opus.
- **thread_id estable end-to-end:** front `useRef(crypto.randomUUID())` lo genera por montaje y lo manda en cada `/chat`; backend `select_chat_input` elige input INCREMENTAL (`{"messages":[Human]}`, no pisa `pending_clarification`/`proposed_action`) vs INICIAL (`new_state`) según `aget_state`, con `logger.warning`+503 si la lectura falla con checkpointer real (no-clobber). El checkpointer Postgres (ya cableado) persiste multi-turno. `chitchat` ve los últimos N mensajes (`short_term_history_window=10`; window=0→vacío).
- **Slot-filling (approach: re-invocar `propose` con overrides):** `Clarification(stage, candidates, prompt)` + `ProposalResult.clarification` + `resolvers.candidates` + `pending_clarification` (state) + `choice_agent.resolve_choice` (12b, índice acotado, fail-closed→0). `clarify_node` + **entry condicional** `START→clarify si pending else router` (NO toca el mecanismo del router); mapea respuesta→candidato y re-invoca `REGISTRY[kind].propose(..., client_override/appointment_override)`, **encadenando cliente→turno**. Las 5 write-tools aceptan AMBOS overrides (log/update/create ignoran appointment_override, pero DEBEN aceptarlo o `clarify_node`→TypeError) + helper `clarify_or_abstain_*`. `propose_appointment` UNIFICADO sobre `resolve_single_client` (ejecutó el fast-follow; el resolver de PROFESIONAL sigue hard-abstención = No-objetivo). No-mapea → limpia pending + reintento (fail-safe, no re-rutea). `confirm_action` limpia `proposed_action`.
- **Gate final:** no-llm **244**, `-m llm` **19/19**, ruff/mypy limpio. Review final (opus) *Ready to merge YES*, sin Critical (HITL airtight by construction; multi-tenant scoped; fail-closed en capas). 2 Important resueltos en fix-wave (no-clobber /chat; test pausa/mensaje).
- **Decisión del usuario (aceptada):** el `ROUTER_PROMPT` se extendió 2 líneas (chitchat reconoce meta-preguntas) pese a "router no cambia" del spec — sin eso las meta-preguntas iban a out_of_scope; anotado en el spec.
- **Hallazgo (systematic-debugging, gotcha LangGraph):** un input de texto plano a un thread pausado en `confirm_action` DESCARTA el interrupt y RE-EJECUTA desde el entry (no re-interrumpe; no escribe; `proposed_action` huérfano inerte). Los tests de slot-filling DEBEN mockear `classify_intent` para ser deterministas/no-llm (eran no-llm pero el 1er turno dependía de Ollama vía el router).
- **Smoke navegador VALIDADO + verificado en DB (2026-06-30):** slot-filling de turno (Ramiro Mansilla — cancela el elegido, deja el otro) + de cliente (Juan ambiguo → elige) + memoria conversacional (chitchat recuerda "Dra. Gómez") + no-mapea (Maite intacta, 0 escrituras) + one-shot (Ambar, tarjeta directa) + SQL/chitchat no-regresión. **LIMITACIÓN conocida (NO bug):** "quién es mi profesional de referencia?" rutea a `sql` (suena a consulta de datos) → no usa la memoria; el Data Agent además a veces inventa un profesional en vez de abstenerse (fast-follow del juez SQL). La memoria conversacional vive en `chitchat`; el follow-up contextual de sql/rag fue No-objetivo del slice.

### Cierre Slice 7 — reschedule_appointment + update_client (2ª mutación) (2026-06-29)
- **Mergeado (`494a511`, `--no-ff`) + PUSHEADO (`23df20c`).** reschedule REUSA `resolve_single_appointment` (Slice 6); extractor 12b `ProposedReschedule{client_name, current_when?, new_start_at}` (new_start obligatorio, current_when opcional desambigua, preserva duración). update_client = campos ESTRUCTURADOS (phone/email/status/dob) por `UPDATE … COALESCE` parcial, **SIN notes** (diferido a Guardrails). Gate no-llm **206**, `-m llm` **16**. Review opus *Ready to merge YES* (único Minor: `_FIELD_LABELS` duplicado). Spec/plan en `docs/superpowers/{specs,plans}/2026-06-29-reschedule-and-update-client*`.

### Cierres previos (condensados — detalle en los specs/plans y en la memoria del proyecto)
- **Slice 6 — cancel_appointment** (1ª mutación, `f3d520b`): `resolve_single_appointment` fail-closed; `db.find_cancellable_appointments` + `db.cancel_appointment` (guard practice_id+status). no-llm 178, -m llm 12.
- **Slice 5 — log_interaction + REGISTRY** (`5138844`): `REGISTRY: dict[str, WriteTool]`, nodos genéricos por `kind`, `resolve_single_client` compartido. no-llm 157.
- **Slice 4 — create_appointment HITL** (`8e0ccfd`): 2 nodos (propose checkpointea + confirm interrupt/resume), transporte SSE confirm + /chat/resume, ConfirmCard. 1ª publicación a origin.
- **Slice 3 — NL2SQL read-only** (`8804a73`): capa semántica + validación sqlglot (practice_id AND-conjunct) + juez intención↔SQL + ejecutor read-only.
- **Slice 2 — CRAG** (`d765eca`): retrieve→rerank→juez relevancia→reformular→síntesis con citas→juez groundedness; fuentes solo en grounded.
- **Slice 1 — grafo + router** (`ae46438`): máquina de estados LangGraph, router e4b, checkpointer AsyncPostgresSaver.
- **Fase 0** (aceptada 2026-06-25): docker-compose, schema, ingesta (parse→chunk→embed→Qdrant), datos sintéticos.

### Comandos útiles
```bash
docker compose up -d
backend\.venv\Scripts\python backend\seed_demo.py                          # 3 prof, 30 clientes, 80 turnos
backend\.venv\Scripts\python backend\dev.py                                # backend :8000 (NO uvicorn)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q       # 244 passed
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q             # 19 passed (requiere Ollama)
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
npm --prefix frontend run dev                                              # front :3000/3001/3002
npm --prefix frontend run test -- --run; npm --prefix frontend run lint; npm --prefix frontend run build
ollama list                                                                # gemma4:12b y gemma4:e4b
```
> Para el smoke LLM real: Ollama corriendo + `docker compose up -d` + schema/seed aplicados. Smoke manual del navegador en `frontend/SMOKE.md`.
