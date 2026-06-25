# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana, y commits LIMPIOS sin ninguna atribución a Claude.
2. La Fase 0 está CERRADA y aceptada, y TODA la limpieza pre-Fase 1 está SALDADA (backend + frontend). No reabras nada de eso. Estamos arrancando FASE 1 (MVP conversacional, alcance en CLAUDE.md §7).

Estado y verificación (todo verde al cierre de la última sesión):
- Backend: `backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q` (27 verdes) + `-m llm` (2 verdes, requiere Ollama + infra). ruff + mypy OK.
- Frontend: `cd frontend; npx vitest run` (10 verdes) + `npx next lint` + `npx next build` OK. `next` ya en 15.5.19.
- Infra: `docker compose up -d` (Postgres + Qdrant). Backend: `backend\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000`. Frontend: `npm --prefix frontend run dev`. (PowerShell NO soporta `cd x && y`.)
- ⚠️ Si el navegador da "error 500" al subir/chatear: es el backend caído (el front proxya `/api/*` a `:8000`). Levantá uvicorn. Stores quedaron RESETEADOS y limpios.

Tarea: ARRANCAR FASE 1 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7.

Punto de arranque RECOMENDADO y a confirmar conmigo: el **grafo LangGraph + router semántico** (es la base de la que cuelgan CRAG, Data Agent NL2SQL, tools con human-in-the-loop, memoria de corto plazo y guardrails). Alternativas si prefiero: empezar por CRAG (mejora directa del RAG que ya anda) o por NL2SQL. Preguntame por dónde arranco antes de escribir código.

Dos ítems ya DIFERIDOS a Fase 1 (no son cleanup): (a) migrar `<Thread>` a `@assistant-ui/react-ui` junto al canvas más rico; (b) la abstención que todavía muestra fuentes que no usó — lo resuelve de raíz el juez de relevancia de CRAG, no parchear el front.
```

---

## Contexto de referencia (para vos / la próxima sesión)

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
backend\.venv\Scripts\python -m uvicorn app.main:app --reload --app-dir backend
backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q     # 19
backend\.venv\Scripts\python -m pytest backend/tests -m llm -q           # 2 (requiere Ollama)
# Frontend
npm --prefix frontend run dev
cd frontend; npx vitest run; npm run lint; npm run build
# Ollama (ya instalado)
ollama list      # debe figurar gemma4:12b
```

> Nota: para correr el smoke LLM real necesitás Ollama corriendo (servicio de Windows, arranca solo) + `docker compose up -d` + schema/seed aplicados. El test usa el app in-process (no hace falta uvicorn). El smoke manual del navegador está en `frontend/SMOKE.md`.
