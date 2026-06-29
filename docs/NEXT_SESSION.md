# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana (HITL), y commits LIMPIOS sin ninguna atribución a Claude.
2. Estamos en FASE 1 (MVP conversacional, alcance en CLAUDE.md §7). Ya están MERGEADOS a `main` y validados (tests + smoke navegador) SIETE slices — NO los reabras:
   - SLICE 1: grafo LangGraph + router semántico (merge ae46438).
   - SLICE 2: subgrafo CRAG correctivo (merge d765eca): retrieve → rerank (bge-reranker-v2-m3) → juez de relevancia → reformular/reintentar → síntesis con citas → juez de groundedness → emitir/abstener. Fuentes SOLO en el camino grounded.
   - SLICE 3: Data Agent NL2SQL read-only (merge 8804a73): NL→SELECT con capa semántica → validación sqlglot (1 sentencia, SELECT-only, practice_id AND-conjunct del WHERE, LIMIT clamp) → juez intención↔SQL → abstención fail-closed → ejecutor read-only. Gotcha: la gen SQL va por TEXTO PLANO + sqlglot (with_structured_output da None).
   - SLICE 4: write-tool create_appointment con HITL (merge 8e0ccfd): DOS nodos — propose_appointment (extrae args tipados + resolver determinístico nombre→UUID/fecha→ISO, se checkpointea) + confirm_appointment (interrupt(proposed_action) → al reanudar con Command(resume=...) escribe o cancela). Transporte: /chat con stream_mode=["custom","updates"] emite evento SSE confirm + POST /chat/resume. Front ConfirmCard.
   - SLICE 5: log_interaction + REGISTRY de write-tools (merge 5138844): nodos genéricos propose_action/confirm_action despachan por proposed_action["kind"] vía REGISTRY (agents/write_tools.py: WriteTool descriptor + classify_write_action + adapters _write_*). resolve_single_client compartido (agents/resolvers.py). Tabla interactions (§5.2). El front ConfirmCard es agnóstico al kind.
   - SLICE 6: write-tool cancel_appointment — 3ª tool, 1ª MUTACIÓN (merge f3d520b, 2026-06-29): primera tool que MUTA una fila (las 2 previas son INSERT). Pieza nueva: resolve_single_appointment (agents/resolvers.py, fail-closed, simétrico a resolve_single_client) — resuelve el turno objetivo entre los turnos futuros cancelables del cliente; el extractor 12b saca ProposedCancellation{client_name, when?} (pista de fecha OPCIONAL), el resolver filtra por día (refina por hora si hay varios el mismo día) y ABSTIENE listando candidatos si 0/>1 (no hay memoria para preguntar "¿cuál?" en otro turno). db.find_cancellable_appointments (futuro + status programado/confirmado, JOIN practitioners, scoped) + db.cancel_appointment (UPDATE status='cancelado', guard practice_id+status, devuelve None en no-match → idempotencia/TOCTOU; NO re-chequea start_at). REGISTRY/CLASSIFY_PROMPT/WRITE_KINDS extendidos (cancel sale de unsupported; create=nuevo vs cancel=existente). nodos.py: copy de capacidades incluye "cancelar turnos" + se sacó el or {} muerto de confirm_action_node. Front ConfirmCard SIN cambios. Spec/plan en docs/superpowers/{specs,plans}/2026-06-29-cancel-appointment*.
   - SLICE 7: write-tools reschedule_appointment + update_client — 4ª y 5ª tools, 2ª MUTACIÓN (merge 494a511, 2026-06-29). reschedule REUSA resolve_single_appointment (Slice 6) sin tocarlo; extractor 12b ProposedReschedule{client_name, current_when?, new_start_at}: new_start OBLIGATORIO (ilegible→abstención datetime_parse_failed, pasado→new_time_past), current_when OPCIONAL (desambigua cuál turno), preserva duración (new_end=new_start+(old_end−old_start)); db.reschedule_appointment (UPDATE start_at/end_at, guard practice_id+status, None→idempotencia/TOCTOU). update_client = campos ESTRUCTURADOS (phone/email/status/dob), UPDATE … COALESCE (parcial, NO borra), SIN notes (texto libre → diferido a Guardrails por §0); db.get_client para el antes→después; reusa resolve_single_client; abstiene no_fields si no hay campos; propose_update_client acepta now por uniformidad del dispatch aunque no lo use. REGISTRY/CLASSIFY_PROMPT (6 kinds: contraste create/cancel/reschedule por verbo, update_client vs cancel por objeto)/WRITE_KINDS extendidos; nodes.py SOLO copy de capacidades (mantiene "agendar turnos"/"cancelar turnos"/"registrar interacciones" contiguos para no romper tests); ConfirmCard SIN cambios. Spec/plan en docs/superpowers/{specs,plans}/2026-06-29-reschedule-and-update-client*.

Estado y verificación (Slice 7 cerrado, 2026-06-29):
- Gate no-llm: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → 206 passed. Lint OK con ORDEN `ruff format` ANTES de `ruff check` (E501 marca dicts/returns largos de código plano pero EXIME pragmas `# type: ignore`; format los envuelve). mypy SIEMPRE con `--config-file backend/pyproject.toml` (sin eso, falso-positivo asyncpg [import-untyped]).
- `-m llm` suite verde 16 passed (Ollama + ambos modelos + Postgres/Qdrant): incluye test_reschedule_e2e_llm.py + test_update_client_e2e_llm.py (confirm → DB cambió; decline → intacto; aserto de interrupt-kind NO-vacuo) + cancel/create/log no-regresión.
- Review final whole-branch (opus): Ready to merge YES (sin Critical/Important; ambas mutaciones parametrizadas + scoped por practice_id + guard de estado + solo alcanzables tras Confirmar; clasificador/registry NO regresan las 3 tools previas; commits limpios). Único Minor nuevo: _FIELD_LABELS duplicado (update_client_agent ↔ write_tools).
- Smoke §2 en navegador VALIDADO (2026-06-29): reprogramar (Confirmar) → start_at movido; update_client teléfono/baja → DB cambió; create (pidió profesional → abstención correcta), log, cancel no-regresión OK; abstención reschedule sin turnos futuros; botón Cancelar no escribe. INVESTIGACIÓN cerrada: el flake del e2e de reschedule fue un TRANSITORIO de Ollama (classify_intent 8/8 action, classify_write 8/8, extracción 12b 10/10), NO sistémico; el router NO lista "reprogramar" pero el e4b lo generaliza a action; el `except Exception` fail-closed vuelve el hiccup en abstención. Decline e2e endurecidos (assert interrupt, no-vacuos) + cubiertos determinísticamente por test_hitl_cycle.
- main PUSHEADO a origin (github.com/salvanya/PRAXIA). Infra: docker compose up -d. Backend: backend\.venv\Scripts\python backend\dev.py (NO uvicorn: ProactorEventLoop vs psycopg async; dev.py es cwd-agnóstico via __file__). Frontend: npm --prefix frontend run dev (Next salta a 3000/3001/3002 si están ocupados). Seed: backend\.venv\Scripts\python backend\seed_demo.py (3 prof, 30 clientes, 80 turnos, determinista). En el harness PowerShell el cwd del tool = backend y un exe por ruta relativa necesita `&`/`.\`; backend bindea 127.0.0.1.

Gotchas vigentes:
- structured-output de e4b devuelve None INTERMITENTE (aun en enums chicos en ciertas frases de acción) → router Y classify_write_action usan ainvoke + text-parse (match exacto→substring, retry, fallback fail-closed: router→chitchat, classify→unsupported). Los args tipados del extractor 12b (with_structured_output) SÍ funcionan. Regla: structured output OK para bool/enum/IDs; para texto libre → plano + validación.
- Agregar imports nuevos a un archivo de tests EXISTENTE a mitad de archivo rompe ruff E402 (config select=["E",...], sin ignore) → los imports de tests van al TOP. (Archivos de test nuevos no tienen el problema.)
- Tests del front: `npm --prefix frontend run test -- --run` (NO `npx --prefix frontend vitest run`: --prefix de npx no fija el cwd → se saltea el config JSX-automatic → "React is not defined").
- Lint: corré `ruff format` ANTES de `ruff check`. E501 marca líneas largas de código plano (dicts/returns) PERO exime las que terminan en pragma `# type: ignore`; `ruff format` envuelve las de código. El orden check→format aborta por E501 en código pegado sin envolver.
- e2e -m llm con modelo local: hiccups/transitorios de Ollama bajo carga → el `except Exception` fail-closed los vuelve abstención (END sin tarjeta). Endurecé los tests decline con `assert snap.next == ("confirm_action",)` (no-vacuos) y reintentá; NO debilites asserts. La clasificación/extracción de reschedule es confiable (verificado 8/8 + 10/10).

Tarea: arrancar el PRÓXIMO SLICE de Fase 1 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7.

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código):
- Guardrails (RECOMENDADO — Presidio PII español + detección de inyección) en entrada/salida del grafo. Sirve la directiva primaria §0 (datos de salud); además el content de log_interaction hoy se almacena SIN redacción (este slice es donde se arregla).
- Memoria de corto plazo real: el front manda un thread_id estable → el checkpointer Postgres persiste multi-turno (hoy new_state mintea un uuid4 por request). También habilita slot-filling: hoy propose_action es one-shot, y cancel_appointment ABSTIENE listando si el cliente tiene varios turnos (no puede preguntar "¿cuál?" en un turno siguiente sin memoria).
- (Las write-tools reschedule_appointment + update_client YA se hicieron en Slice 7. Lo único pendiente de write-tools es update_client.notes — texto libre → va DETRÁS de Guardrails.) El clasificador devuelve unsupported para lo que aún no es tool.

Fast-follows fichados (NO bloquean): normalizar `when`/`new_start_at` aware-no-UTC con astimezone(UTC) en reschedule_agent + cancel_agent + action_agent (display-vs-write: la tarjeta/recibo etiquetan "(UTC)" sobre el wall-clock; solo aplica si el LLM desobedece el prompt y devuelve offset no-UTC — el review final lo subió de cosmético por ser mismatch HITL); consolidar _FIELD_LABELS (duplicado update_client_agent ↔ write_tools); appt_resolve_limit dedicado para el finder (hoy reusa appt_name_match_limit=5); golden case si el clasificador real confunde create↔cancel↔reschedule (hoy 8/8 ok); unificar el resolver de cliente de propose_appointment sobre resolve_single_client; logging.warning si db.get_client devuelve None en propose_update_client; denylist SQL (pg_read_file/pg_sleep); audit log (agent_runs)+consents; created_by (necesita auth real); timezone por práctica (hoy todo UTC, etiquetado).

Ítems DIFERIDOS a Fase 1: migrar <Thread> a @assistant-ui/react-ui + canvas rico (tablas/fichas/citas/tarjetas de confirmación); botón "Editar" en la tarjeta; afinar el copy de los botones de la ConfirmCard para cancelaciones ("Sí, cancelar"/"No" en vez de Confirmar/Cancelar, que en una tarjeta de cancelar lee raro); afinar el prompt del router con DSPy (Fase 2) — caso límite conocido: "¿atienden los domingos?" rutea a sql en vez de rag.
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 7 — reschedule_appointment + update_client (4ª y 5ª write-tools, 2ª mutación) (2026-06-29)
- **Mergeado (`494a511`, `--no-ff`) a `main` y PUSHEADO a `origin`.** 8 commits de impl + 2 de docs, autoría limpia. Spec/plan en `docs/superpowers/{specs,plans}/2026-06-29-reschedule-and-update-client*`. Rama `fase-1/slice-7-reschedule-update-client` borrada. Ejecución subagent-driven (7 tasks TDD + 1 fix-wave: T7 endurecer decline e2e). Modelos: haiku para transcripción (T1–4,7), sonnet para wiring sobre archivos existentes (T5,T6), reviewers sonnet, review final opus.
- **reschedule_appointment** (2ª mutación) REUSA `resolve_single_appointment` (Slice 6) — el 80% del riesgo (resolver ambiguo, scoping, fail-closed) ya estaba. Lo nuevo: DOS referencias temporales (current_when opcional desambigua + new_start_at obligatorio) y preservar duración. Rechaza fecha pasada al proponer; el writer guarda por estado (no re-chequea now, simétrico a cancel).
- **update_client** = campos ESTRUCTURADOS (phone/email/status/dob) por `UPDATE … COALESCE` parcial (nunca borra un campo). **EXCLUYE `notes`** (texto libre → PII sin redacción → diferido a Guardrails, §0). `db.get_client` para el antes→después en la tarjeta. La impl NO mete `before` en params (más limpio que el sketch del spec, confirmado por el review). Decisión: truthiness en la recolección de campos (evita clear accidental, consistente con el no-clear).
- **Gate final:** no-llm **206**, `-m llm` **16** (incl. 4 e2e nuevos), ruff/mypy limpio. Review final (opus) *Ready to merge YES*, sin Critical/Important; único Minor nuevo = _FIELD_LABELS duplicado.
- **INVESTIGACIÓN (el e2e de reschedule jitterea):** el reviewer cazó que los tests decline pasaban VACUOS; al endurecerlos (assert interrupt) saltó un fallo. Diagnóstico = NO es debilidad de clasificación: classify_intent **8/8 action**, classify_write **8/8 reschedule**, extracción 12b **10/10** (nombre real+sintético, fecha futura). Fue un TRANSITORIO de Ollama bajo carga que el `except Exception` fail-closed volvió abstención (END). Hipótesis "el router no lista reprogramar" DESCARTADA (el e4b generaliza "reprogramá"→action sin estar en los ejemplos). Decline endurecidos + re-corridos verdes; la seguridad del decline ya está determinística en test_hitl_cycle (5 kinds).
- **Gotcha dev-loop nuevo:** lint = `ruff format` ANTES de `ruff check` (E501 marca código plano largo pero exime `# type: ignore`). En el harness, PowerShell corre con cwd=backend y un exe relativo necesita `&`/`.\`; `dev.py` es cwd-agnóstico (usa `__file__`). Frontend: Next salta a 3000/3001/3002 si hay puertos ocupados.

### Cierre Slice 6 — cancel_appointment (3ª write-tool, 1ª mutación) (2026-06-29)
- **Mergeado (`f3d520b`, `--no-ff`) a `main` y PUSHEADO a `origin`.** 8 commits de implementación + 2 de docs, autoría limpia. Spec/plan en `docs/superpowers/{specs,plans}/2026-06-29-cancel-appointment*`. Rama `slice-6-cancel-appointment` borrada (historia preservada en el merge).
- **Ejecución subagent-driven** (6 tasks TDD + 2 fix-waves del review: T2 = E402 import al top + 2 tests de ramas de desambiguación; T6 = endurecer el seed helper del e2e para no filtrar el cliente). Modelos: sonnet para implementers que editan archivos existentes (T1/T2/T4/T5/T6) + haiku para el archivo nuevo de transcripción pura (T3); reviewers sonnet; review final whole-branch opus.
- **Diseño clave — primera MUTACIÓN sobre el registry:** las 2 write-tools previas son INSERT puros; cancelar MUTA una fila existente, así que la pieza nueva es resolver **cuál** turno. `resolve_single_appointment` (en `resolvers.py`, simétrico a `resolve_single_client`, fail-closed): cliente → `db.find_cancellable_appointments` (futuro + `programado`/`confirmado`, JOIN practitioners, scoped por `practice_id`) → si vino la pista de fecha filtra por día (refina por hora si hay varios el mismo día) → exactamente 1 propone; 0/>1 abstiene listando candidatos. Decisión #4 (dos guards distintos): el **finder** define lo ofrecible (futuro); el **writer** (`db.cancel_appointment`) define lo mutable (guard `practice_id`+`status`, `None` en no-match → idempotencia/TOCTOU; NO re-chequea `start_at` para no fallar por "confirmaste 2 min tarde").
- **Desambiguación con pista de fecha (Decisión #3):** como NO hay memoria de corto plazo, un "¿cuál turno?" no se puede responder en un turno siguiente → el extractor saca `client_name` + `when` OPCIONAL y el resolver desambigua de entrada; si igual queda ambiguo, abstiene listando (fail-closed, nunca elige el turno por el usuario).
- **Registry/nodos:** `_write_cancel` (envuelve el `None` del writer en `{"cancelled": False}` → recibo cordial "⚠️ ya no estaba disponible"), `format_cancel_receipt`, alta en `REGISTRY`, `WRITE_KINDS`+`CLASSIFY_PROMPT` extendidos. `nodes.py` solo cambió copy (capacidades incluye "cancelar turnos") + se sacó el `or {}` muerto de `confirm_action_node` (fast-follow de Slice 5). **Front `ConfirmCard` SIN cambios** (agnóstico al kind, dividendo del registry).
- **Gate final:** no-llm **178**, `-m llm` **12** (incl. 2 e2e de cancel, pasaron a la primera con modelos reales), mypy 35, ruff limpio. Review final (opus) *Ready to merge YES*, sin Critical/Important.
- **Smoke navegador VALIDADO (2026-06-29) + verificado en DB:** cancelar+Confirmar → turno `cancelado` (Julia Valdez 30/06, Maite 01/07); **desambiguación por fecha** canceló el turno correcto de Maite (01/07) y dejó el otro (13/07) intacto; botón Cancelar (Benicio 01/07) y abstenciones NO escribieron; `unsupported` (reprogramar) mostró el copy de capacidades; create (Ambar, reason NULL) y log (Julia) no-regresión OK. cancel = solo UPDATE de status (conserva `reason` del seed).
- **Gotcha de dev-loop nuevo:** agregar imports a un archivo de tests EXISTENTE a mitad de archivo rompe ruff E402 → imports de tests al TOP (lo introdujo T2, lo cazó el review). **Fast-follows del review** (no bloquean): `appt_resolve_limit` dedicado; `when`→`astimezone(UTC)`; golden create↔cancel; unificar el resolver de cliente de `propose_appointment`.

### Cierre Slice 5 — log_interaction + registry de write-tools (2026-06-28)
- **Mergeado (`5138844`, `--no-ff`).** 16 commits, autoría limpia. Spec/plan/addenda en `docs/superpowers/{specs,plans}/2026-06-28-log-interaction*`. + fix de front follow-up `2d01ec6` (key en `ConfirmCard`). **PUSHEADO a `origin`** al cierre.
- **Diseño clave — registry de dispatch** (elegido sobre router-intents-finos): `REGISTRY: dict[str, WriteTool]` en `agents/write_tools.py`. `propose_action_node` clasifica el `kind` (`classify_write_action` e4b) y delega `REGISTRY[kind].propose`; `confirm_action_node` hace `interrupt(action)` → `REGISTRY[kind].write/.format_receipt`. Escala a N tools sin tocar router/transporte/front. `action_agent.py` (turnos) quedó INTACTO; `resolve_single_client` (`resolvers.py`) compartido.
- **Hallazgo (deviation aprobada):** `with_structured_output` de e4b devuelve `None` **intermitente** (~1/3) en frases de acción → router Y `classify_write_action` pasaron a `ainvoke` + parseo de texto (mismo patrón que el SQL agent). El ROL del router no cambió (sigue grueso, 5 intents).
- **Smoke navegador VALIDADO (2026-06-29).** Confirmar escribió 1 fila en `interactions`; Cancelar/abstenciones 0; turno no-regresión + `unsupported` OK.

### Cierre Slice 4 — write-tool create_appointment con HITL (2026-06-28)
- **Mergeado (`8e0ccfd`, `--no-ff`) y VALIDADO en navegador + DB.** Reemplazó `action_stub` por la 1ª tool de escritura con human-in-the-loop. Spec/plan en `docs/superpowers/{specs,plans}/2026-06-27-write-appointment-hitl*`.
- **2 nodos (clave):** `propose` (LLM extrae args tipados + resolver determinístico, se checkpointea) + `confirm` (interrupt → al reanudar escribe exactamente lo confirmado, sin recompute). Transporte `/chat` (custom+updates → SSE confirm) + `POST /chat/resume`.
- **Decisiones:** `created_by` NULL (sin auth → Fase 4); hora UTC etiquetada; tool in-process (no MCP server), cumple §4 en espíritu.
- **`main` PUSHEADO a `origin`** al cierre — 1ª publicación real (datos sintéticos, `.env` gitignored).

### Cierre Slice 3 NL2SQL + validación Fase 1 en navegador (2026-06-27)
- **Fase 1 VALIDADA en navegador (7/7)**: chitchat sin fuentes; ingesta indexada; CRAG con cita; abstención SIN fuentes; conteo de turnos real; listado multi-tenant; acción de escritura → stub.
- **Bug menor arreglado (`0821ed4`):** `render_rows_markdown` imprimía celdas NULL como `"None"` → helper `_fmt` mapea `None`→vacío; seeder carga `email`/`phone`.

### Cierre sesión de limpieza pre-Fase 1 (2026-06-25)
- **Limpieza backend + frontend saldada.** Smoke tras la limpieza: 503 amable con Ollama caído (`6eae047`); stores reseteados (fuentes duplicadas eran datos sucios).

### Estado al cierre de la sesión de aceptación de Fase 0 (2026-06-25)
- **Fase 0 aceptada y cerrada.** Ollama 0.30.x; `gemma4:12b` confirmado. Smoke real verde (ingesta → chat citado en streaming → abstención). Bugs de parsers SSE arreglados (`aafbf68`).

### Fase 1 — alcance (CLAUDE.md §7 / Blueprint §6)
Grafo LangGraph + router semántico ✅ · Agentic RAG correctivo (CRAG) ✅ · Data Agent NL2SQL + capa semántica ✅ · tools de escritura con human-in-the-loop (`interrupt`) ✅ (`create_appointment`, `log_interaction`, `cancel_appointment`) · **memoria de corto plazo (checkpointer Postgres) + memoria semántica básica** (pendiente) · **guardrails (Presidio PII español + inyección)** (pendiente) · caching (semántico + embeddings) · eval mínima (golden set + juez `e4b`) + trazas Phoenix · frontend más rico (canvas: tablas, fichas, citas, vista de docs, tarjetas de confirmación).

### Ítems de limpieza diferidos
- **Backend / Frontend — SALDADO** (ver cierres previos): 503 amable, dedup de ingesta, guarda de dim de embeddings, bump de `next` a 15.5.x, `TextDecoder` fatal, etc.
- **Frontend — DIFERIDO a Fase 1:** migrar `<Thread>` a `@assistant-ui/react-ui` (en 0.7.91 `Thread` NO está deprecado; trae Tailwind/CSS, es alcance del "canvas" más rico junto a tablas/fichas/citas/tarjetas).

### Comandos útiles
```bash
# Infra
docker compose up -d
docker compose exec -T postgres psql -U praxia -d praxia < backend/app/schema.sql
backend\.venv\Scripts\python backend\seed_demo.py    # datos demo (3 profesionales, 30 clientes, 80 turnos)
# Backend (PowerShell: no usar 'cd x && y')
backend\.venv\Scripts\python backend\dev.py        # runner con fix Windows (SelectorEventLoop)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q     # 206 passed
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q           # e2e (requiere Ollama) — 16 passed
backend\.venv\Scripts\python -m mypy backend/app --config-file backend/pyproject.toml
# Frontend
npm --prefix frontend run dev
npm --prefix frontend run test -- --run; npm --prefix frontend run lint; npm --prefix frontend run build
# Ollama (ya instalado)
ollama list      # debe figurar gemma4:12b y gemma4:e4b
```

> Nota: para el smoke LLM real necesitás Ollama corriendo + `docker compose up -d` + schema/seed aplicados. El smoke manual del navegador está en `frontend/SMOKE.md`.
