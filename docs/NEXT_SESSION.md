# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana, y commits LIMPIOS sin ninguna atribución a Claude.
2. Estamos en FASE 1 (MVP conversacional, alcance en CLAUDE.md §7). Ya están MERGEADOS a `main` y VALIDADOS (tests + smoke en navegador) TRES slices — NO los reabras:
   - SLICE 1: grafo LangGraph + router semántico (merge ae46438).
   - SLICE 2: subgrafo CRAG correctivo (merge d765eca + fix 7b0da07): retrieve → rerank (bge-reranker-v2-m3) → juez de relevancia → reformular/reintentar (cap 2) → síntesis buffered con citas → juez de groundedness → emitir o abstener. Fuentes SOLO en el camino grounded (cerró el bug de "abstención que muestra fuentes que no usó"). Spec/plan en docs/superpowers/{specs,plans}/2026-06-26-crag*.
   - SLICE 3: Data Agent NL2SQL read-only (merge 8804a73 + fix 0821ed4): NL→`SELECT` con capa semántica (`semantic_layer/model.yaml`) → validación sqlglot (1 sentencia, SELECT-only, allow-list, `practice_id` como AND-conjunct del WHERE externo, rechazo SELECT INTO, LIMIT clamp) → juez intención↔SQL → retry cap 2 → abstención fail-closed → ejecutor read-only (tx READ ONLY + statement_timeout + tope filas) → síntesis grounded (números verbatim + tabla markdown). `sql_node` enchufado; **`action_stub` INTACTO**. Gotcha clave: `with_structured_output` devuelve None para la gen SQL en Gemma local → se genera por TEXTO PLANO + sqlglot (que ES la decodificación restringida real, §4). Spec/plan en docs/superpowers/{specs,plans}/2026-06-26-nl2sql*.

Estado y verificación (todo verde al cierre, 2026-06-27):
- Gate no-llm: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` → **106 passed**. ruff OK. mypy: correr SIEMPRE con `--config-file backend/pyproject.toml` (sin eso, desde la raíz, da falso-positivo asyncpg [import-untyped]; con la config = limpio).
- `-m llm` e2e verde (requiere Ollama + ambos modelos + Postgres/Qdrant + pesos de bge-reranker-v2-m3 ya descargados ~600MB).
- **Smoke en navegador VALIDADO 7/7**: chitchat sin fuentes · ingesta protocolo.md indexado · CRAG con cita [1] · abstención SIN fuentes · "¿cuántos turnos esta semana?" → 20 reales · listado de clientes multi-tenant (seeder ahora con email/phone, commit 0821ed4) · acción de escritura → stub "próximo slice".
- Infra: `docker compose up -d` (Postgres + Qdrant). Backend: `backend\.venv\Scripts\python backend\dev.py` (NO `python -m uvicorn` directo: ProactorEventLoop vs psycopg async del checkpointer). Frontend: `npm --prefix frontend run dev` (si :3000 lo tiene un node viejo, Next usa :3001). PowerShell NO soporta `cd x && y`. Si el navegador da "error 500": backend caído o front stale; `localhost` puede resolver a `::1`, el backend bindea `127.0.0.1`.
- Ollama: `gemma4:12b` (síntesis) y `gemma4:e4b` (router/jueces) pulled. `main` local 55 commits adelante de origin/main (NO pusheado).

Tarea: arrancar el PRÓXIMO SLICE de Fase 1 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7.

Punto de arranque a confirmar conmigo (preguntame ANTES de escribir código):
- **Tools de escritura con human-in-the-loop** (reemplaza `action_stub`, RECOMENDADO — cierra el último stub del grafo): tools MCP parametrizadas (`create_appointment`, `log_interaction`) detrás de un `interrupt` de LangGraph con tarjeta de confirmación. Hay un TODO breadcrumb en `action_stub`. Nota: los args estructurados (bool/enum/ID) SÍ funcionan con structured output, a diferencia del texto-libre SQL.
- **Guardrails** (Presidio PII español + inyección de prompt) en entrada/salida del grafo.
- **Memoria de corto plazo real**: hoy `new_state` mintea un `uuid4` por request → el checkpointer Postgres no persiste multi-turno; falta que el front mande un `thread_id` estable.

Hardening fichado (NO bloquea; traer cuando entre input no confiable o multi-tenant): denylist de funciones SQL peligrosas (`pg_read_file`/`pg_sleep`); per-tabla join scoping + RLS = Fase 4.

Ítems DIFERIDOS a Fase 1 (no son cleanup): migrar `<Thread>` a `@assistant-ui/react-ui` junto al canvas más rico (tablas/fichas/citas/tarjetas de confirmación); afinar el prompt del router con DSPy (Fase 2) contra un golden set — caso límite conocido: "¿atienden los domingos?" rutea a sql en vez de rag.
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Cierre Slice 3 NL2SQL + validación Fase 1 en navegador (2026-06-27)
- **Fase 1 VALIDADA en navegador (7/7)**: chitchat sin fuentes; ingesta `protocolo.md` indexado; CRAG con cita `[1]`; abstención SIN fuentes; "¿cuántos turnos esta semana?" → 20 turnos reales; listado de clientes multi-tenant; acción de escritura → stub "próximo slice".
- **Bug menor hallado en el smoke y arreglado (commit `0821ed4`):** `render_rows_markdown` imprimía celdas NULL como el literal `"None"` (`str(None)`) → helper `_fmt` mapea `None`→vacío (y el camino escalar de `_deterministic`); y el seeder de clientes ahora carga `email`/`phone` (Faker) con backfill `ON CONFLICT (id) DO UPDATE` (ids determinísticos). Tests nuevos: render NULL→vacío (`test_sql_present`) + clientes con email (`test_seed_demo`, integración). Gate no-llm **106 passed**.
- Servidores frenados al cierre. `main` local **55 commits adelante de `origin/main` (NO pusheado** — convención local-first; pedir OK explícito antes de pushear datos de un CRM de salud).

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
Grafo LangGraph + router semántico · Agentic RAG correctivo (CRAG: reranker `bge-reranker-v2-m3` + jueces de relevancia/groundedness) · Data Agent NL2SQL + capa semántica (`semantic_layer/model.yaml`) · tools de escritura con human-in-the-loop (`interrupt`) · memoria de corto plazo (checkpointer Postgres) + memoria semántica básica · guardrails (Presidio PII español + inyección) · caching (semántico + embeddings) · eval mínima (golden set + juez `e4b`) + trazas Phoenix · frontend más rico (canvas: tablas, fichas, citas, vista de docs, tarjetas de confirmación).

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
# Backend (PowerShell: no usar 'cd x && y')
backend\.venv\Scripts\python backend\dev.py        # runner con fix Windows (SelectorEventLoop)
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q     # 19
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q           # 2 (requiere Ollama)
# Frontend
npm --prefix frontend run dev
cd frontend; npx vitest run; npm run lint; npm run build
# Ollama (ya instalado)
ollama list      # debe figurar gemma4:12b
```

> Nota: para correr el smoke LLM real necesitás Ollama corriendo (servicio de Windows, arranca solo) + `docker compose up -d` + schema/seed aplicados. El test usa el app in-process (no hace falta uvicorn). El smoke manual del navegador está en `frontend/SMOKE.md`.
