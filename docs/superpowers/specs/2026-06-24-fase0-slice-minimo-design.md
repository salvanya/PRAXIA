# Praxia — Fase 0 · Slice Vertical Mínimo — Diseño

**Fecha:** 2026-06-24 · **Fase:** 0 (Fundaciones e ingesta) · **Estado:** Aprobado (diseño) · **Próximo paso:** plan de implementación (`writing-plans`)

> Este documento es el *qué* del primer slice ejecutable de Praxia. El *cómo trabajar* vive en `CLAUDE.md`; el diseño completo del producto en `Praxia_Blueprint.md`. Este spec respeta ambos y **difiere deliberadamente** todo lo que sea Fase 1+.

---

## 1. Objetivo

Construir el **loop end-to-end más delgado posible** que demuestre la tesis de Praxia: *soltás un documento y le hablás a tus datos*.

Concretamente: soltar **un** documento operativo (un protocolo o política de la práctica) → indexarlo → preguntar en lenguaje natural → recibir una respuesta en español **con citas**, todo aislado por `practice_id`.

Es un slice **vertical**: toca todas las capas (infra → ingesta → vector store → retrieval → LLM → frontend) pero cada una en su versión mínima. El valor es probar que el cableado funciona de punta a punta y dejar la base sobre la que Fase 1 construye el sistema agéntico.

---

## 2. Decisiones de alcance (tomadas en brainstorming)

| Decisión | Elección | Por qué |
|---|---|---|
| Tamaño | **Slice vertical mínimo** | Probar el loop completo cuanto antes; ensanchar cada capa después. |
| Interfaz | **Next.js + assistant-ui (mínimo)** | Fidelidad con el frontend destino del Blueprint, aceptando el andamiaje de build/TS. |
| Documento ancla | **Operativo / conocimiento (sin PII)** — protocolo/política, scope `practice_id`, sin `client_id` | Evita Presidio/PII (guardrail de Fase 1) y da un demo limpio. El esquema igual soporta `client_id` para después. |
| Enfoque técnico | **Lean** — parsers livianos, pipeline RAG lineal (sin LangGraph), bge-m3 denso | docling y LangGraph no aportan nada en un `retrieve→sintetizar` plano; Fase 1 los adopta cuando se justifican (routing, HITL, OCR, docs reales). |

---

## 3. Definition of Done (criterios de aceptación)

1. `docker compose up -d` levanta **Postgres + Qdrant** (con healthchecks y volúmenes nombrados).
2. Aplicar `schema.sql` crea el esquema de §5.2 del Blueprint; un `seed.sql` inserta **una** práctica demo con UUID fijo (= `PRACTICE_ID`).
3. Desde la UI, soltás un PDF o MD → el documento queda `indexado`: fila en `documents` (status `procesando→indexado`) + chunks en la colección Qdrant.
4. Preguntás algo **cubierto** por el documento → respuesta **en streaming**, en español, **con al menos una cita** que referencia título y (si aplica) página.
5. Preguntás algo **no cubierto** → el modelo **se abstiene** explícitamente ("no lo encuentro en los documentos"), no alucina. *(Anti-alucinación por prompt + cortocircuito sin contexto; el juez de groundedness es Fase 1.)*
6. **Aislamiento multi-tenant:** todo retrieval filtra por `practice_id`; un chunk de otra práctica nunca aparece (cubierto por test).
7. `ruff check`, `ruff format --check`, `mypy app/` y `pytest -q` pasan en verde.
8. **Cero red saliente** desde el código de Praxia más allá de Ollama/Qdrant/Postgres locales (la descarga única de modelos —Gemma, bge-m3— es setup, no runtime).

---

## 4. Arquitectura

```
┌──────────────────────────┐                ┌─────────────────────────────────┐
│ Frontend (Next.js)       │  POST /ingest  │ Backend (FastAPI, async)        │
│ • assistant-ui <Thread>  │ ─────────────► │ /ingest    (multipart)          │
│ • <DropZone>             │  POST /chat    │ /chat      (SSE stream)         │
│ • runtime adapter → SSE  │ ◄───SSE──────  │ /documents · /health            │
└──────────────────────────┘                └───────┬─────────────────────────┘
                                  ┌──────────────────┼───────────────────┐
                                  ▼                  ▼                   ▼
                          ingest/pipeline      rag/retrieve         rag/synthesize
                          parse→chunk→embed     Qdrant cosine        ChatOllama (Gemma)
                                │     │         (filtra practice_id)  stream + citas
                                ▼     ▼               │                    │
                           Postgres  Qdrant ◄─────────┘                    ▼
                          (documents)(praxia_chunks 1024d cos)        tokens SSE
```

**Piezas transversales:**
- **bge-m3** (`sentence-transformers`, 1024 dims, `normalize_embeddings=True`) cargado **una sola vez** como singleton lazy; compartido por ingesta (embeber chunks) y chat (embeber la query). Solo el vector **denso** (sparse/ColBERT se difieren).
- **Qdrant**: una colección `praxia_chunks`, distancia **Cosine**, tamaño 1024.
- **Postgres**: se aplica el `schema.sql` completo (§5.2); el slice solo *usa* `practices` + `documents`.
- **Ollama/Gemma**: `langchain-ollama.ChatOllama`, modelo desde `.env` (`OLLAMA_MODEL`), `base_url` local, streaming vía `.astream()`, temperatura baja (≈0.1).

> **Nota de diseño — sin LangGraph todavía:** el slice es un pipeline lineal `retrieve → synthesize`. El grafo de estados, el router semántico y los MCP servers se introducen en Fase 1, cuando hay múltiples intenciones, HITL y checkpointing que orquestar. Meterlos ahora sería andamiaje no ejercitado (viola §7 "no construir de más").

---

## 5. Componentes (cada uno = unidad con propósito único, testeable)

### Backend `backend/app/`

| Módulo | Responsabilidad | Interfaz (firma conceptual) | Depende de |
|---|---|---|---|
| `config.py` | Settings tipadas desde `.env` | `Settings` (pydantic-settings) | — |
| `db.py` | Pool async Postgres; CRUD mínimo de `documents` | `insert_document()`, `set_document_status()`, `list_documents(practice_id)` | asyncpg |
| `vectorstore.py` | Cliente Qdrant; bootstrap + upsert + search | `ensure_collection()`, `upsert_chunks()`, `search(vector, practice_id, top_k)` | qdrant-client |
| `embeddings.py` | Singleton bge-m3 | `embed_texts(list[str])→list[vec]`, `embed_query(str)→vec` | sentence-transformers |
| `ingest/parse.py` | Extraer texto + mapa de páginas | `parse(bytes, filename)→ParsedDoc{text, pages:list[(page_no, text)]}` | pypdf |
| `ingest/chunk.py` | Trocear con overlap, preservando página | `chunk(parsed)→list[Chunk]` — **función pura** | langchain-text-splitters |
| `ingest/pipeline.py` | Orquestar ingesta completa | `ingest_document(bytes, filename, doc_type, title)→DocumentSummary` | db, parse, chunk, embeddings, vectorstore |
| `rag/retrieve.py` | Recuperar chunks relevantes | `retrieve(query, practice_id, top_k)→list[Chunk]` | embeddings, vectorstore |
| `rag/synthesize.py` | Generar respuesta citada en streaming | `synthesize_stream(query, chunks)→AsyncIterator[str]` + fuentes | langchain-ollama |
| `main.py` | App FastAPI, endpoints, startup | — | todo lo anterior |

**Tipos de dominio (mínimos):**
```python
class Chunk(TypedDict):
    text: str
    page: int | None
    chunk_index: int
    document_id: str
    title: str
    doc_type: str
```

### Frontend `frontend/`

| Archivo | Responsabilidad |
|---|---|
| `app/page.tsx` | Layout: `<DropZone>` + `<Thread>` de assistant-ui en una sola pantalla. |
| `components/DropZone.tsx` | Drag & drop → `POST /api/ingest` (multipart) → muestra estado (procesando→indexado/error) + lista desde `/api/documents`. |
| `lib/runtime.ts` | `useLocalRuntime` con un `ChatModelAdapter` custom: llama `POST /api/chat`, lee el SSE, emite deltas de texto y procesa el evento `sources`. |
| `next.config.*` | `rewrites` `/api/* → http://localhost:8000/*` para evitar CORS en dev. |

---

## 6. Modelo de datos usado por el slice

**Postgres (subset del `schema.sql`):**
- `practices` — una fila demo sembrada (`id = PRACTICE_ID`).
- `documents` — una fila por documento subido: `{id, practice_id, client_id=NULL, doc_type, title, file_uri, mime_type, page_count, status, ingested_at}`.

**Qdrant — colección `praxia_chunks`:**
- Vector: 1024 dims, Cosine.
- Point id: UUID por chunk.
- Payload:
```json
{
  "practice_id": "<uuid>",
  "document_id": "<uuid>",
  "doc_type": "protocolo",
  "title": "Protocolo de primera consulta",
  "page": 2,
  "chunk_index": 5,
  "text": "<contenido del chunk>"
}
```
- **Filtro obligatorio en retrieve:** `practice_id == settings.PRACTICE_ID`.

> **Gotcha (CLAUDE.md §9):** bge-m3 = 1024 dims; la colección debe crearse con ese tamaño o el upsert falla. `ensure_collection()` lo fija explícito.

---

## 7. Flujos detallados

### 7.1 Ingesta
1. `DropZone` envía el archivo a `POST /ingest` (multipart).
2. `pipeline.ingest_document`:
   a. Inserta fila en `documents` con `status='procesando'`.
   b. `parse` → texto + páginas (PDF: por página vía pypdf; MD/TXT: una sola "página" lógica, `page=None`).
   c. `chunk` → trocea **por página** (cada chunk conserva su `page`), overlap configurable.
   d. `embed` → vectores bge-m3 (batch).
   e. `upsert_chunks` → Qdrant con payload completo.
   f. Actualiza `documents.status='indexado'` + `page_count`.
   g. Ante cualquier error → `status='error'` y respuesta 422 con detalle.
3. Devuelve `{document_id, status, n_chunks}`; la UI refleja el estado.

### 7.2 Consulta
1. El chat envía `{message}` a `POST /chat` (respuesta SSE).
2. `retrieve(message, PRACTICE_ID, TOP_K)` → embebe la query, busca en Qdrant filtrando por práctica, devuelve top-k chunks.
3. **Si no hay chunks** → se emite directamente la respuesta de abstención, **sin llamar al modelo**.
4. `synthesize_stream(message, chunks)` arma el prompt y hace streaming con ChatOllama.
5. SSE al frontend:
   - `event: token` → cada delta de texto.
   - `event: sources` → al final, JSON `[{n, title, page, document_id}]`.
   - `event: done`.
6. assistant-ui renderiza la respuesta incremental; las fuentes se listan bajo el mensaje.

### 7.3 Citas y abstención (prompt)
- **System prompt estable** (aprovecha prefix cache): rol + reglas. En español.
- **Contexto numerado**:
  ```
  [1] (Fuente: "Protocolo de primera consulta" — p.2)
  <texto del chunk 1>

  [2] (Fuente: "Política de cancelaciones" — p.1)
  <texto del chunk 2>
  ```
- **Reglas inyectadas:** "Respondé en español SOLO con la información de los fragmentos. Citá con `[n]` las fuentes usadas. Si la respuesta no está en los fragmentos, decí exactamente que no la encontrás en los documentos. No inventes."
- El backend mapea cada `[n]` a su fuente para el evento `sources`.

---

## 8. Manejo de errores y tenancy

| Caso | Respuesta |
|---|---|
| MIME no soportado | 415 + mensaje |
| Parseo falla / texto vacío | `documents.status='error'`, 422 con detalle |
| Sin chunks recuperados | Abstención determinista, sin llamar al modelo |
| Ollama caído | 503: "Ollama no responde en {url}; ¿está corriendo y el modelo {model} pulled?" |
| Qdrant / Postgres caídos | 503 + `/health` lo refleja |

**Tenancy (no negociable, §0.5):** `practice_id` lo inyecta **el servidor** desde config (en el slice no hay auth todavía), **nunca** viene de texto libre del cliente. El filtro de retrieval por `practice_id` es obligatorio y está cubierto por test.

---

## 9. Configuración e infra

**`.env` (vars mínimas; `.env.example` commiteado, `.env` gitignored):**
```
DATABASE_URL=postgresql://praxia:praxia@localhost:5432/praxia
QDRANT_URL=http://localhost:6333
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:12b
EMBED_MODEL=BAAI/bge-m3
PRACTICE_ID=00000000-0000-0000-0000-000000000001
CHUNK_SIZE=1000
CHUNK_OVERLAP=150
TOP_K=5
```

**`docker-compose.yml`:** `postgres:16` (healthcheck, volumen) + `qdrant/qdrant` pin a versión (puertos 6333/6334, volumen). **Ollama no va en compose**: corre nativo en el host para usar la GPU (RTX 4090).

**Dependencias backend (`requirements.txt`):** `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `asyncpg`, `qdrant-client`, `sentence-transformers`, `pypdf`, `langchain-text-splitters`, `langchain-ollama`, `python-multipart`, `sse-starlette`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`, `httpx`.

**Frontend:** Next.js (App Router, TypeScript) + `@assistant-ui/react`.

---

## 10. Estructura del repo (solo lo que el slice necesita)

```
praxia/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── vectorstore.py
│   │   ├── embeddings.py
│   │   ├── schema.sql            # DDL §5.2 (completo)
│   │   ├── seed.sql              # 1 práctica demo
│   │   ├── ingest/
│   │   │   ├── __init__.py
│   │   │   ├── parse.py
│   │   │   ├── chunk.py
│   │   │   └── pipeline.py
│   │   └── rag/
│   │       ├── __init__.py
│   │       ├── retrieve.py
│   │       └── synthesize.py
│   └── tests/
│       ├── conftest.py
│       ├── fixtures/             # 1 PDF + 1 MD de muestra (protocolo)
│       ├── test_parse.py
│       ├── test_chunk.py
│       ├── test_retrieve.py
│       ├── test_pipeline.py
│       └── test_rag_smoke.py
└── frontend/
    ├── package.json
    ├── next.config.*
    ├── app/
    │   ├── layout.tsx
    │   └── page.tsx
    ├── components/
    │   └── DropZone.tsx
    └── lib/
        └── runtime.ts
```

**Mapeo a la estructura canónica de `CLAUDE.md §3` (desviaciones deliberadas):**
- `app/rag/` reemplaza temporalmente a `agents/rag_agent.py` + `mcp_servers/mcp_rag.py`. En Fase 1, la lógica de retrieval/síntesis se refactoriza hacia un MCP server (`mcp_rag`) y un subgrafo agéntico (CRAG); este módulo plano es el precursor.
- No se crean `graph/`, `agents/`, `mcp_servers/`, `memory/`, `guardrails/`, `semantic_layer/`, `eval/`, `caching/`, `dspy_optim/` todavía: pertenecen a fases posteriores.

---

## 11. Estrategia de tests

| Test | Tipo | Qué valida |
|---|---|---|
| `test_chunk.py` | Unit puro | Tamaños, overlap, propagación de página, texto vacío y bordes. |
| `test_parse.py` | Unit | Fixture PDF + MD → texto esperado y mapa de páginas. |
| `test_retrieve.py` | Integración (Qdrant) | Dado chunks sembrados, la query devuelve los esperados; **el filtro `practice_id` excluye otra práctica**. |
| `test_pipeline.py` | Integración (PG + Qdrant) | Ingesta completa deja fila `indexado` + N chunks; ruta de error deja `error`. |
| `test_rag_smoke.py` | Integración con **LLM fake** | Monkeypatch de ChatOllama por un stub que ecoa el contexto → verifica el cableado de citas y la abstención sin necesitar el modelo. Un smoke **real opt-in** (marker `@pytest.mark.llm`) hace una corrida contra Ollama. |

Las integraciones que requieren infra se marcan (`@pytest.mark.integration`) y usan los contenedores dockerizados; los unit puros corren sin infra. Lint + tipos (`ruff`, `mypy`) son parte del gate.

---

## 12. Tarea 0 del plan — de-riskear Ollama/Gemma (antes de tocar código)

1. Verificar `ollama --version ≥ 0.20.2` (instalar si falta; en Windows puede requerir correr el instalador interactivamente).
2. `ollama pull gemma4:12b`. **Si el tag no existe** (no verificable desde el conocimiento del asistente): listar tags `gemma*` disponibles, elegir el mejor y setear `OLLAMA_MODEL` en `.env`. **El código lee el modelo del env → no hay cambio de código.**
3. Confirmar un `generate` trivial con streaming vía `langchain-ollama`.
4. bge-m3 se descarga solo en el primer embed (~2.3 GB, una vez).

---

## 13. Riesgos del slice y mitigaciones

| Riesgo | Mitigación |
|---|---|
| El tag `gemma4:12b` o Ollama ≥0.20.2 no existen tal cual | Tarea 0: verificar/instalar y fallback de modelo vía `.env`. |
| Descarga inicial de modelos (Gemma ~8 GB, bge-m3 ~2.3 GB) | Setup único con red (permitido por §0); no es runtime. |
| API del runtime de assistant-ui varía por versión | Pinnear versión y seguir su doc del `ChatModelAdapter`. |
| Chunking por página genera chunks chicos / corta contexto | Aceptable en el slice; el chunking semántico es refinamiento de Fase 1. |
| Repo bajo OneDrive (`node_modules`, `.next`) puede sincronizar/lockear | `.gitignore` + considerar excluir esas carpetas de OneDrive; los volúmenes de Docker son nombrados (no bind a OneDrive). |

---

## 14. Fuera de alcance (diferido a fases posteriores, §7)

LangGraph + router semántico · MCP servers · reranker (`bge-reranker-v2-m3`) + jueces de relevancia/groundedness (CRAG) · Presidio/PII + guardrail de inyección · NL2SQL / Data Agent / capa semántica · tools de escritura + human-in-the-loop · memoria (corto/largo plazo) · caching (semántico/embeddings) · docling/OCR/audio multimodal · generador sintético Faker+Gemma (el slice usa **un** documento de muestra hecho a mano; el dataset sintético completo es expansión posterior de Fase 0) · DSPy · vLLM · RLS multi-tenant.

---

*Fin del spec. Al aprobarse, el siguiente paso es el plan de implementación (`writing-plans`), que desglosa esto en tareas ordenadas por dependencias.*
