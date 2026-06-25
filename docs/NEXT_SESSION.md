# Praxia — Prompt para la próxima sesión

> Pegá el bloque de abajo en una sesión nueva de Claude Code (parado en la raíz del repo) para continuar el desarrollo. El resto del archivo es contexto de referencia.

---

## ⤵️ PROMPT (copiar y pegar)

```
Vas a continuar el desarrollo de Praxia (CRM conversacional local-first). Antes de actuar:

1. Leé `CLAUDE.md` (contrato operativo, está gitignoreado/local) y `Praxia_Blueprint.md` (diseño completo). Respetá el contrato: inferencia 100% local vía Ollama, costo $0, aislamiento multi-tenant por `practice_id` siempre, escrituras solo por tools con confirmación humana, y commits LIMPIOS sin ninguna atribución a Claude.
2. La Fase 0 (slice vertical mínimo) ya está MERGEADA en `main`. Está descripta en `docs/superpowers/specs/2026-06-24-fase0-slice-minimo-design.md` y sus dos planes en `docs/superpowers/plans/`. Backend en `backend/` (FastAPI + RAG), frontend en `frontend/` (Next.js + assistant-ui).

Estado y verificación:
- Tests automatizados verdes: backend `cd . && backend/.venv/Scripts/python -m pytest backend/tests -m "not llm" -q` (19) y frontend `cd frontend && npm run test` (7). Lint/tipos/build OK.
- PENDIENTE DE ACEPTACIÓN: el demo en vivo con el modelo real (Gemma vía Ollama) nunca se corrió porque Ollama no estaba instalado. Es lo PRIMERO a cerrar (ver "Tarea inmediata").

Tarea inmediata (cerrar la aceptación de Fase 0):
- Verificá Ollama: `ollama --version` (>= 0.20.2). Si no está, instalalo (`winget install Ollama.Ollama`).
- `ollama pull gemma4:12b`. ⚠️ El tag `gemma4:12b` NO está verificado; si no existe, corré `ollama list`, elegí el mejor modelo gemma disponible y seteá `OLLAMA_MODEL` en `.env` (el código lo lee del env, sin cambios de código).
- Levantá infra y servicios: `docker compose up -d`; aplicá `backend/app/schema.sql` y `backend/app/seed.sql`; `uvicorn` (backend) y `npm run dev` (frontend).
- Corré el smoke real: `backend/.venv/Scripts/python -m pytest backend/tests/test_e2e_llm.py -m llm -v` y seguí `frontend/SMOKE.md` en el navegador. Confirmá DoD #4 (respuesta citada real) y #5 (abstención del modelo real).

Después de eso, arrancá Fase 1 usando el flujo de trabajo de siempre: brainstorming → spec → plan (writing-plans) → ejecución subagent-driven con review por tarea. No construyas de más; respetá el alcance por fase de CLAUDE.md §7.

Decime por dónde querés arrancar Fase 1 (ver lista en `docs/NEXT_SESSION.md`), o si preferís primero saldar los ítems de limpieza diferidos.
```

---

## Contexto de referencia (para vos / la próxima sesión)

### Lo que quedó construido en Fase 0 (mergeado en `main`, commit de merge `992771a`)

**Backend (`backend/`, FastAPI async):** docker-compose (Postgres + Qdrant), `schema.sql` (§5.2) + `seed.sql` (1 práctica demo), pipeline de ingesta `parse→chunk→embed(bge-m3 1024d)→Qdrant + fila documents`, retrieval coseno **filtrado por `practice_id`**, síntesis con citas + abstención (ChatOllama), API `/health` `/ingest` `/chat`(SSE) `/documents`. 19 tests (`-m "not llm"`).

**Frontend (`frontend/`, Next.js + assistant-ui):** proxy `/api/*`→`:8000`, cliente API, lector SSE (`lib/chatStream.ts`), adapter de runtime assistant-ui, DropZone + DocumentList, pantalla única con chat en streaming y bloque *Fuentes*. 7 tests (vitest).

### Fase 1 — alcance (CLAUDE.md §7 / Blueprint §6)
Grafo LangGraph + router semántico · Agentic RAG correctivo (CRAG: reranker `bge-reranker-v2-m3` + jueces de relevancia/groundedness) · Data Agent NL2SQL + capa semántica (`semantic_layer/model.yaml`) · tools de escritura con human-in-the-loop (`interrupt`) · memoria de corto plazo (checkpointer Postgres) + memoria semántica básica · guardrails (Presidio PII español + inyección) · caching (semántico + embeddings) · eval mínima (golden set + juez `e4b`) + trazas Phoenix · frontend más rico (canvas: tablas, fichas, citas, vista de docs, tarjetas de confirmación).

### Ítems de limpieza diferidos de Fase 0 (de los reviews; integrarlos en Fase 1)
- **Backend:** validar dim de embeddings vs `settings.embed_dim` (guarda del gotcha 1024) · test del error-path del pipeline + `_mime` más completo · anotación de retorno `/ingest` → `dict[str, Any]` · renombrar el param `app` del `lifespan` · `/chat` debe devolver 503 amable si Ollama está caído (spec §8) · `practice_id` hardcodeado → multi-tenant real cuando llegue auth (Fase 4).
- **Frontend:** bump de `next@15.1.3` (avisos de `npm audit`) dentro de 15.x antes de exponer · migrar `<Thread>` (deprecado en 0.7.91) a `@assistant-ui/react-ui` · `TextDecoder` con `{fatal:true}` en `chatStream.ts` · exportar y testear `sourcesBlock`/`lastUserText` · persistir `NEXT_TELEMETRY_DISABLED` vía `.env.local`.

### Comandos útiles
```bash
# Infra
docker compose up -d
docker compose exec -T postgres psql -U praxia -d praxia < backend/app/schema.sql
docker compose exec -T postgres psql -U praxia -d praxia < backend/app/seed.sql
# Backend
backend/.venv/Scripts/python -m uvicorn app.main:app --reload --app-dir backend
backend/.venv/Scripts/python -m pytest backend/tests -m "not llm" -q
# Frontend
cd frontend && npm run dev
cd frontend && npm run test && npm run build
```

> Nota: el ledger de ejecución de Fase 0 (`.superpowers/sdd/progress.md`) es scratch local gitignoreado; puede no existir en un clon nuevo. Esta lista de diferidos es la copia durable.
