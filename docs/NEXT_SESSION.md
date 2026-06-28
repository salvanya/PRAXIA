# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana, y commits LIMPIOS sin ninguna atribución a Claude.
2. Estamos en FASE 1 (MVP conversacional, alcance en CLAUDE.md §7). Ya están MERGEADOS a `main` y VALIDADOS (tests + smoke en navegador) CUATRO slices — NO los reabras:
   - SLICE 1: grafo LangGraph + router semántico (merge ae46438).
   - SLICE 2: subgrafo CRAG correctivo (merge d765eca + fix 7b0da07): retrieve → rerank (bge-reranker-v2-m3) → juez de relevancia → reformular/reintentar (cap 2) → síntesis buffered con citas → juez de groundedness → emitir o abstener. Fuentes SOLO en el camino grounded. Spec/plan en docs/superpowers/{specs,plans}/2026-06-26-crag*.
   - SLICE 3: Data Agent NL2SQL read-only (merge 8804a73 + fix 0821ed4): NL→`SELECT` con capa semántica → validación sqlglot (1 sentencia, SELECT-only, allow-list, `practice_id` AND-conjunct del WHERE externo, rechazo SELECT INTO, LIMIT clamp) → juez intención↔SQL → retry cap 2 → abstención fail-closed → ejecutor read-only (tx READ ONLY + statement_timeout + tope filas) → síntesis grounded. Gotcha: `with_structured_output` devuelve None para la gen SQL en Gemma local → TEXTO PLANO + sqlglot (decodificación restringida real, §4). Spec/plan en docs/superpowers/{specs,plans}/2026-06-26-nl2sql*.
   - SLICE 4: write-tool `create_appointment` con human-in-the-loop (merge 8e0ccfd, 2026-06-28): reemplazó `action_stub`. DOS NODOS (clave): `propose_appointment` (LLM extrae args tipados `ProposedAppointment` + resolver determinístico nombre→UUID y fecha→ISO, scoped por `practice_id`, fail-closed) se CHECKPOINTEA; `confirm_appointment` hace `interrupt(proposed_action)` y al reanudar con `Command(resume="confirm"|"cancel")` escribe vía `db.create_appointment` (INSERT parametrizado + guard `EXISTS(... AND practice_id)`) o cancela. Separar en 2 nodos evita que el LLM se re-ejecute al reanudar (el interrupt re-corre el nodo desde arriba) → se escribe EXACTAMENTE lo confirmado. Transporte: `/chat` con `stream_mode=["custom","updates"]` emite evento SSE `confirm`; nuevo `POST /chat/resume` (determinístico, sin probe de Ollama). Front mínimo: `ConfirmCard` (Confirmar/Cancelar, recibo en la card) + `resumeChat`; `useChatRuntime(onConfirm?)`. Hora etiquetada `(UTC)`. Spec/plan en docs/superpowers/{specs,plans}/2026-06-27-write-appointment-hitl*.

Estado y verificación (todo verde al cierre, 2026-06-28):
- Gate no-llm: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → **130 passed**. ruff check + format OK. mypy SIEMPRE con `--config-file backend/pyproject.toml` (sin eso, falso-positivo asyncpg [import-untyped]).
- `-m llm` e2e verde (requiere Ollama + ambos modelos + Postgres/Qdrant; bge-reranker ~600MB ya descargado): incluye `test_action_e2e_llm.py` (confirm escribe +1, cancel no escribe). Front: vitest 14/14 + lint + build.
- **Smoke en navegador VALIDADO (HITL, 2026-06-28)**: tarjeta de confirmación → Confirmar escribe la fila (verificado en DB: 1 turno, Bautista Garcia/Martina Gomez); Cancelar y abstenciones (sin profesional con 3 activos / cliente inexistente / ambiguo) NO escriben. + lo previo (chitchat sin fuentes, SQL conteo real, listado, scope reject).
- Infra: `docker compose up -d`. Backend: `backend\.venv\Scripts\python backend\dev.py` (NO `uvicorn` directo: ProactorEventLoop vs psycopg async del checkpointer). Frontend: `npm --prefix frontend run dev` (:3000/:3001). Seed: `backend\.venv\Scripts\python backend\seed_demo.py` (3 profesionales, 30 clientes, 80 turnos). PowerShell NO soporta `cd x && y`; backend bindea `127.0.0.1` (`localhost` puede ser `::1`).
- Ollama: `gemma4:12b` (síntesis/extracción) y `gemma4:e4b` (router/jueces) pulled. `main` ya PUSHEADO a `origin` (github.com/salvanya/PRAXIA) al cierre de esta sesión.

Tarea: arrancar el PRÓXIMO SLICE de Fase 1 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7.

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código):
- **`log_interaction`** (RECOMENDADO — 2ª write-tool, fast-follow: la maquinaria HITL ya existe; reusás `propose_appointment_node`/`confirm_appointment_node` como patrón, solo otra tool parametrizada detrás del mismo `interrupt`). Requiere PORTAR la tabla `interactions` del blueprint (§5.2, "el corazón del CRM"; hoy NO existe en schema.sql).
- **Guardrails** (Presidio PII español + inyección de prompt) en entrada/salida del grafo.
- **Memoria de corto plazo real**: hoy `new_state` mintea un `uuid4` por request → el checkpointer Postgres no persiste multi-turno; falta que el front mande un `thread_id` estable. (También habilita slot-filling: hoy la propuesta de turno es one-shot — si falta un dato, abstiene.)

Hardening fichado (NO bloquea): denylist de funciones SQL peligrosas (`pg_read_file`/`pg_sleep`); per-tabla join scoping + RLS = Fase 4; audit log (`agent_runs`) + `consents`; `created_by` en appointments (necesita auth real); timezone por práctica (hoy todo UTC, etiquetado en tarjeta/recibo).

Ítems DIFERIDOS a Fase 1: migrar `<Thread>` a `@assistant-ui/react-ui` + canvas rico (tablas/fichas/citas/tarjetas de confirmación); botón "Editar" en la tarjeta; afinar el prompt del router con DSPy (Fase 2) — caso límite conocido: "¿atienden los domingos?" rutea a sql en vez de rag.
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 4 — write-tool create_appointment con HITL (2026-06-28)
- **Slice 4 mergeado (`8e0ccfd`, `--no-ff`) y VALIDADO en navegador + DB.** Reemplazó `action_stub` por la 1ª tool de escritura con human-in-the-loop. 13 commits, autoría limpia. Spec/plan en `docs/superpowers/{specs,plans}/2026-06-27-write-appointment-hitl*`.
- **Ejecución subagent-driven** (8 tareas TDD + 1 fix consolidado del review final). Review final whole-branch (opus): *Ready to merge YES* — verificó contra los internals reales de langgraph 0.2.76: no hay escritura sin confirmación; sin recompute de la propuesta al reanudar (2 nodos: `propose` checkpointeado + `confirm` con `interrupt`); idempotencia de doble-submit (`map_command`: el RESUME solo lo consume un interrupt pendiente); multi-tenant scoped en resolvers + guard `EXISTS` en el INSERT.
- **Smoke navegador (HITL) validado:** Confirmar escribió la fila (verificado en DB — la tool crea con `reason`/`channel` NULL, así que `reason IS NULL` aísla lo creado por HITL: 1 fila, Bautista Garcia/Martina Gomez/2026-06-29 10:00 UTC/programado); Cancelar y las 3 abstenciones NO escribieron (total 81 = 80 seed + 1).
- **Hallazgo de ejecución (T8):** el e2e inicial falló honestamente — el seed tiene **3 profesionales activos**, así que una frase sin profesional abstiene `practitioner_unspecified` (correcto, fail-closed) y nunca abre la tarjeta. Fix nombró un profesional → ejercita el HITL real y cubre la rama de profesional nombrado. Confirma la regla del Slice 3: structured output SÍ sirve para args tipados (bool/enum/str/ID), a diferencia del texto-libre SQL.
- **Decisiones del slice:** `created_by` NULL (sin auth → Fase 4); hora mostrada/almacenada en UTC, etiquetada `(UTC)` en tarjeta/recibo (tz por práctica → Fase 4); tool **in-process** (no MCP server), cumple §4 en espíritu. Front mínimo funcional (recibo dentro de la `ConfirmCard`; el canvas rico + append al hilo siguen diferidos).
- **`main` PUSHEADO a `origin`** (github.com/salvanya/PRAXIA) al cierre de esta sesión — 1ª publicación real (antes era local-first sin push). Datos del repo = sintéticos (Faker), `.env` gitignored.

### Cierre Slice 3 NL2SQL + validación Fase 1 en navegador (2026-06-27)
- **Fase 1 VALIDADA en navegador (7/7)**: chitchat sin fuentes; ingesta `protocolo.md` indexado; CRAG con cita `[1]`; abstención SIN fuentes; "¿cuántos turnos esta semana?" → 20 turnos reales; listado de clientes multi-tenant; acción de escritura → stub "próximo slice".
- **Bug menor hallado en el smoke y arreglado (commit `0821ed4`):** `render_rows_markdown` imprimía celdas NULL como el literal `"None"` (`str(None)`) → helper `_fmt` mapea `None`→vacío (y el camino escalar de `_deterministic`); y el seeder de clientes ahora carga `email`/`phone` (Faker) con backfill `ON CONFLICT (id) DO UPDATE` (ids determinísticos). Tests nuevos: render NULL→vacío (`test_sql_present`) + clientes con email (`test_seed_demo`, integración). Gate no-llm **106 passed**.
- Servidores frenados al cierre. (Histórico: en ese momento `main` estaba 55 commits adelante de `origin/main`, sin pushear; YA PUSHEADO en el cierre del Slice 4.)

### Cierre sesión de limpieza pre-Fase 1 (2026-06-25)
- **Limpieza backend + frontend saldada** (ver "Ítems de limpieza diferidos" más abajo). Commits: `b695414`, `b226fc2`, `7281a1a`, `28e2729`, `7b02f5d`, `6eae047`.
- **Smoke en navegador** tras la limpieza: encontró y se arreglaron dos cosas — (#3) con Ollama caído el chat mostraba burbuja vacía → ahora renderiza el mensaje 503 amable (`6eae047`); (#1) fuentes duplicadas eran datos sucios de tests → **stores reseteados** (`documents` vaciada, colección `praxia_chunks` recreada 1024/Cosine). (#2) abstención-muestra-fuentes quedó diferido a CRAG.
- Suite al cierre: backend 27 (no-llm) + 2 (llm) verdes; frontend 10 verdes, lint+build OK.

### Estado al cierre de la sesión de aceptación de Fase 0 (2026-06-25)
- **Fase 0 aceptada y cerrada.** Ollama 0.30.9 instalado; `gemma4:12b` confirmado y pulled (el tag SÍ existe; `OLLAMA_MODEL` queda como está). Smoke real verde en el navegador: ingesta → chat citado en streaming → abstención.
- **Bugs hallados y arreglados durante la aceptación** (commit `aafbf68`, todos en tests/parsers, no en lógica de producto):
  1. `frontend/lib/chatStream.ts` — el parser SSE buscaba `\n\n` pero `sse_starlette` usa CRLF (`\r\n\r\n`) → el mensaje salía VACÍO en el navegador. Ahora tolera `\r?\n`. (era el síntoma "el chat no responde nada").
  2. `backend/tests/test_e2e_llm.py` — el aserto concatenaba líneas SSE crudas y el modelo tokeniza `60`→`6`,`0`; ahora reconstruye el texto desde los payloads `data:` de eventos `token`. Se sumó test de abstención real (DoD #5).
  3. `backend/tests/conftest.py` — `sse_starlette` mantiene un `should_exit_event` global ligado al loop del 1er test de streaming; se resetea por test para permitir varios tests SSE en una corrida.

### Fase 1 — alcance (CLAUDE.md §7 / Blueprint §6)
Grafo LangGraph + router semántico ✅ · Agentic RAG correctivo (CRAG) ✅ · Data Agent NL2SQL + capa semántica ✅ · tools de escritura con human-in-the-loop (`interrupt`) ✅ (`create_appointment`; falta `log_interaction`) · memoria de corto plazo (checkpointer Postgres) + memoria semántica básica · guardrails (Presidio PII español + inyección) · caching (semántico + embeddings) · eval mínima (golden set + juez `e4b`) + trazas Phoenix · frontend más rico (canvas: tablas, fichas, citas, vista de docs, tarjetas de confirmación).

### Ítems de limpieza diferidos (saldar ANTES de Fase 1)
- **Backend — SALDADO (commits `b695414`, `b226fc2`, `7281a1a`):**
  - ✅ `/chat` devuelve **503 amable si Ollama está caído** (probe a `/api/version` antes de abrir el stream SSE).
  - ✅ **Dedup de ingesta** por `sha256(contenido) + practice_id` (índice único en DB + reuso de fila; self-heal ante drift PG/Qdrant).
  - ✅ **Guarda de dim de embeddings** vs `settings.embed_dim` (gotcha 1024) con mensaje claro.
  - ✅ Test del **error-path del pipeline** (status `error`) + `_mime` cubre `.pdf/.txt/.md/.markdown/desconocido`.
  - ✅ Anotación de retorno de `/ingest` → `dict[str, Any]`.
  - ✅ `lifespan(_app)` deja de sombrear el `app` global.
  - ✅ `practice_id` hardcodeado → TODO claro de multi-tenant real (Fase 4).
- **Frontend — SALDADO (commits `28e2729`, `7b02f5d`):**
  - ✅ Bump de `next` 15.1.3 → **15.5.19** (dentro de 15.x): resuelve las advisories de Next.js de `npm audit` (cache poisoning RSC, XSS, SSRF, DoS). Lint/test/build verdes. Quedan advisories de tooling de dev (vitest→esbuild/vite) y del postcss bundleado por Next: solo dev/test loop, no runtime; su fix son majors con breaking changes.
  - ✅ `TextDecoder("utf-8", {fatal:true})` en `chatStream.ts` + test de char multibyte partido entre chunks.
  - ✅ `NEXT_TELEMETRY_DISABLED` en `frontend/.env.local` (gitignored por convención Next, per-máquina; documentado en `.env.example` para clones nuevos).
- **Frontend — DIFERIDO a Fase 1 (no es cleanup de bajo riesgo):**
  - Migrar `<Thread>` a `@assistant-ui/react-ui`. **Nota:** en la versión instalada (`@assistant-ui/react` 0.7.91) `Thread` NO está deprecado (sin `@deprecated`, build limpio); migrar trae el paquete `react-ui` + wiring de Tailwind/CSS (el `Thread` bundleado trae sus estilos; el de `react-ui` no). Eso es alcance de "frontend más rico" de Fase 1, no limpieza. Hacerlo junto al canvas (tablas/fichas/citas/tarjetas de confirmación).

### Comandos útiles
```bash
# Infra
docker compose up -d
docker compose exec -T postgres psql -U praxia -d praxia < backend/app/schema.sql
docker compose exec -T postgres psql -U praxia -d praxia < backend/app/seed.sql
backend\.venv\Scripts\python backend\seed_demo.py    # datos demo (3 profesionales, 30 clientes, 80 turnos)
# Backend (PowerShell: no usar 'cd x && y')
backend\.venv\Scripts\python backend\dev.py        # runner con fix Windows (SelectorEventLoop)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q     # 130 passed
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q           # e2e (requiere Ollama)
# Frontend
npm --prefix frontend run dev
cd frontend; npx vitest run; npm run lint; npm run build
# Ollama (ya instalado)
ollama list      # debe figurar gemma4:12b y gemma4:e4b
```

> Nota: para correr el smoke LLM real necesitás Ollama corriendo (servicio de Windows, arranca solo) + `docker compose up -d` + schema/seed aplicados. El test usa el app in-process (no hace falta uvicorn). El smoke manual del navegador está en `frontend/SMOKE.md`.
