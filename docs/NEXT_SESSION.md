# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana, y commits LIMPIOS sin ninguna atribución a Claude.
2. La Fase 0 (slice vertical mínimo) está MERGEADA en `main` y su aceptación está CERRADA: el smoke en vivo con Gemma real (gemma4:12b vía Ollama) pasó end-to-end en el navegador — respuesta citada (DoD #4) y abstención (DoD #5). Ollama 0.30.9 ya está instalado y el modelo `gemma4:12b` pulled.

Estado y verificación:
- Backend: `backend/.venv/Scripts/python -m pytest backend/tests -m "not llm" -q` (19 verdes) + LLM real `... -m llm` (2 verdes, requiere Ollama + infra). ruff + mypy OK.
- Frontend: `cd frontend && npx vitest run` (8 verdes) + `npm run lint` + `npm run build` OK.
- Infra: `docker compose up -d` (Postgres + Qdrant). Levantar backend con `backend\.venv\Scripts\python -m uvicorn app.main:app --reload --app-dir backend` y frontend con `npm --prefix frontend run dev` (PowerShell no soporta `cd x && y`).

Tarea de esta sesión (ANTES de Fase 1): saldar los ítems de limpieza diferidos listados en `docs/NEXT_SESSION.md` (sección "Ítems de limpieza diferidos"). Son chicos y de bajo riesgo; agrupalos en pocos commits temáticos. Priorizá:
- `/chat` debe devolver un 503 amable si Ollama está caído (spec §8) — hoy probablemente rompe feo.
- Dedup de ingesta: re-indexar el mismo documento crea filas duplicadas en "Documentos" (visto en el smoke). Dedup por hash de contenido + `practice_id`.
- Validar dim de embeddings vs `settings.embed_dim` (guarda del gotcha 1024) y test del error-path del pipeline.

Después de la limpieza, arrancá Fase 1 con el flujo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7. Decime por dónde querés arrancar Fase 1 (ver lista de alcance abajo).
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Estado al cierre de esta sesión (2026-06-25)
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
- **Frontend (pendiente — "antes de exponer", no bloquea Fase 1):**
  - Bump de `next@15.1.3` dentro de 15.x (avisos de `npm audit`) antes de exponer.
  - Migrar `<Thread>` (deprecado en 0.7.91) a `@assistant-ui/react-ui`.
  - `TextDecoder` con `{fatal:true}` en `chatStream.ts`.
  - Persistir `NEXT_TELEMETRY_DISABLED` vía `.env.local`.

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
