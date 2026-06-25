# Fase 0 · Slice Mínimo · Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend of Praxia's thinnest vertical slice — a FastAPI service that ingests one operational document (PDF/MD), indexes it (parse→chunk→embed→Qdrant + Postgres row), and answers questions over it in Spanish with citations and grounded abstention, all isolated by `practice_id`.

**Architecture:** Linear async RAG pipeline (no LangGraph yet). Ingestion: `parse → chunk → embed(bge-m3) → upsert Qdrant + documents row`. Query: `embed query → Qdrant cosine search (filtered by practice_id) → ChatOllama(Gemma) streaming with cited answer`. FastAPI exposes `/health`, `/ingest`, `/chat` (SSE), `/documents`.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, qdrant-client (async), sentence-transformers (bge-m3), pypdf, langchain-text-splitters, langchain-ollama, sse-starlette, pytest/ruff/mypy. Infra via Docker: postgres:16, qdrant.

## Global Constraints

- **Inferencia 100% local:** todo LLM por Ollama en `http://localhost:11434`. Prohibido llamar APIs de LLM externas. (CLAUDE.md §0.1)
- **Costo $0:** solo OSS / self-host. (CLAUDE.md §0.2)
- **Multi-tenant siempre:** toda query y todo retrieval filtra por `practice_id`. (CLAUDE.md §0.5)
- **Async en todo el I/O.** Python 3.11+. (CLAUDE.md §1)
- **Embeddings bge-m3 = 1024 dims:** la colección Qdrant debe crearse con `size=1024` o el upsert falla. (CLAUDE.md §9)
- **Modelo LLM parametrizado por env** (`OLLAMA_MODEL`), nunca hardcodeado — el tag `gemma4:12b` no está verificado y puede requerir fallback.
- **Secretos solo por `.env`**, nunca commiteados; mantener `.env.example`. (CLAUDE.md §2)
- **Commits limpios:** sin trailer `Co-Authored-By: Claude`, sin atribución al asistente. (CLAUDE.md §6)
- **Gate antes de cerrar cada task:** `ruff check .`, `ruff format --check .`, `mypy app/`, `pytest -q` en verde.

**Spec de referencia:** `docs/superpowers/specs/2026-06-24-fase0-slice-minimo-design.md`

---

## File Structure

```
praxia/
├── docker-compose.yml          # postgres:16 + qdrant
├── .env.example                # vars de config (committeado)
├── .gitignore
└── backend/
    ├── requirements.txt
    ├── pyproject.toml          # config de ruff + mypy + pytest
    ├── app/
    │   ├── __init__.py
    │   ├── main.py             # FastAPI: /health /ingest /chat /documents
    │   ├── config.py           # Settings (pydantic-settings)
    │   ├── db.py               # pool asyncpg + CRUD documents
    │   ├── vectorstore.py      # AsyncQdrantClient: ensure/upsert/search
    │   ├── embeddings.py       # singleton bge-m3
    │   ├── schema.sql          # DDL §5.2 (completo)
    │   ├── seed.sql            # 1 práctica demo
    │   ├── models.py           # tipos de dominio (Chunk, DocumentSummary)
    │   ├── ingest/
    │   │   ├── __init__.py
    │   │   ├── parse.py        # parse(bytes, filename) -> ParsedDoc
    │   │   ├── chunk.py        # chunk(parsed) -> list[Chunk]  (pura)
    │   │   └── pipeline.py     # ingest_document(...) -> DocumentSummary
    │   └── rag/
    │       ├── __init__.py
    │       ├── retrieve.py     # retrieve(query, practice_id, top_k)
    │       └── synthesize.py   # synthesize_stream(query, chunks, llm=None)
    └── tests/
        ├── __init__.py
        ├── conftest.py
        ├── fixtures/protocolo.md
        ├── test_config.py
        ├── test_chunk.py
        ├── test_parse.py
        ├── test_synthesize.py
        ├── test_db.py            # @integration
        ├── test_vectorstore.py   # @integration
        ├── test_pipeline.py      # @integration
        ├── test_retrieve.py      # @integration
        └── test_api.py           # @integration (FastAPI + fakes)
```

All commands below run from the repo root `praxia/` unless noted. Backend Python commands run inside a venv: `python -m venv backend/.venv && backend/.venv/Scripts/activate` (Windows).

---

## Task 1: Scaffolding + infra (Docker, config files, package skeleton)

**Files:**
- Create: `docker-compose.yml`, `.env.example`, `.gitignore`
- Create: `backend/requirements.txt`, `backend/pyproject.toml`
- Create: `backend/app/__init__.py`, `backend/app/ingest/__init__.py`, `backend/app/rag/__init__.py`, `backend/tests/__init__.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_infra.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a running Postgres (`localhost:5432`) and Qdrant (`localhost:6333`), an installable backend package, and the pytest `integration` marker used by all later integration tests.

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: praxia
      POSTGRES_PASSWORD: praxia
      POSTGRES_DB: praxia
    ports:
      - "5432:5432"
    volumes:
      - praxia_pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U praxia"]
      interval: 5s
      timeout: 3s
      retries: 10

  qdrant:
    image: qdrant/qdrant:v1.12.4
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - praxia_qdrant:/qdrant/storage

volumes:
  praxia_pg:
  praxia_qdrant:
```

- [ ] **Step 2: Create `.env.example`**

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

- [ ] **Step 3: Create `.gitignore`**

```
# Python
__pycache__/
*.pyc
backend/.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
# Env / secrets
.env
# Models cache
.cache/
# Node / Next (frontend)
node_modules/
frontend/.next/
# OS
Thumbs.db
.DS_Store
```

- [ ] **Step 4: Create `backend/requirements.txt`**

```
fastapi==0.115.*
uvicorn[standard]==0.32.*
pydantic-settings==2.*
asyncpg==0.30.*
qdrant-client==1.12.*
sentence-transformers==3.*
pypdf==5.*
langchain-text-splitters==0.3.*
langchain-ollama==0.2.*
python-multipart==0.0.*
sse-starlette==2.*
pytest==8.*
pytest-asyncio==0.24.*
httpx==0.27.*
ruff==0.7.*
mypy==1.13.*
```

- [ ] **Step 5: Create `backend/pyproject.toml`** (tool config)

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
disallow_untyped_defs = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: requires running Postgres/Qdrant (deselect with -m 'not integration')",
    "llm: requires a running Ollama with the configured model",
]
```

- [ ] **Step 6: Create empty package files**

Create these files, each empty:
`backend/app/__init__.py`, `backend/app/ingest/__init__.py`, `backend/app/rag/__init__.py`, `backend/tests/__init__.py`.

- [ ] **Step 7: Create `backend/tests/conftest.py`** (load `.env` for tests)

```python
import os
from pathlib import Path

# Load .env into the environment for tests if present.
env_path = Path(__file__).resolve().parents[2] / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
```

- [ ] **Step 8: Write the failing infra test** — `backend/tests/test_infra.py`

```python
import os

import asyncpg
import httpx
import pytest


@pytest.mark.integration
async def test_postgres_reachable():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    value = await conn.fetchval("SELECT 1")
    await conn.close()
    assert value == 1


@pytest.mark.integration
def test_qdrant_reachable():
    resp = httpx.get(os.environ["QDRANT_URL"] + "/readyz", timeout=5)
    assert resp.status_code == 200
```

- [ ] **Step 9: Bring up infra and install deps**

```bash
cp .env.example .env
docker compose up -d
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt
```

- [ ] **Step 10: Run the infra test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_infra.py -m integration -v`
Expected: PASS (2 passed). If Postgres isn't ready yet, wait a few seconds and retry.

- [ ] **Step 11: Commit**

```bash
git add docker-compose.yml .env.example .gitignore backend/
git commit -m "chore: scaffolding e infra (docker, deps, package backend)"
```

---

## Task 2: Configuration (`config.py`)

**Files:**
- Create: `backend/app/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: env vars from Task 1.
- Produces: `Settings` dataclass and a cached `get_settings() -> Settings` with fields: `database_url: str`, `qdrant_url: str`, `ollama_base_url: str`, `ollama_model: str`, `embed_model: str`, `practice_id: str`, `chunk_size: int`, `chunk_overlap: int`, `top_k: int`, `qdrant_collection: str` (constant `"praxia_chunks"`), `embed_dim: int` (constant `1024`).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_config.py`

```python
from app.config import get_settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:12b")
    monkeypatch.setenv("PRACTICE_ID", "00000000-0000-0000-0000-000000000001")
    get_settings.cache_clear()
    s = get_settings()
    assert s.ollama_model == "gemma4:12b"
    assert s.qdrant_collection == "praxia_chunks"
    assert s.embed_dim == 1024
    assert s.top_k == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Implement `backend/app/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://praxia:praxia@localhost:5432/praxia"
    qdrant_url: str = "http://localhost:6333"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:12b"
    embed_model: str = "BAAI/bge-m3"
    practice_id: str = "00000000-0000-0000-0000-000000000001"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    top_k: int = 5

    # Constants (not from env)
    qdrant_collection: str = "praxia_chunks"
    embed_dim: int = 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_config.py
git commit -m "feat: settings tipadas desde .env"
```

---

## Task 3: Domain types + DB layer (`models.py`, `schema.sql`, `seed.sql`, `db.py`)

**Files:**
- Create: `backend/app/models.py`, `backend/app/schema.sql`, `backend/app/seed.sql`, `backend/app/db.py`
- Test: `backend/tests/test_db.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces:
  - `models.Chunk` (TypedDict: `text:str, page:int|None, chunk_index:int, document_id:str, title:str, doc_type:str`)
  - `models.DocumentSummary` (TypedDict: `document_id:str, status:str, n_chunks:int`)
  - `db.get_pool() -> asyncpg.Pool` (cached), `db.insert_document(practice_id, doc_type, title, file_uri, mime_type) -> str` (returns document_id, status `procesando`), `db.set_document_status(document_id, status, page_count=None) -> None`, `db.list_documents(practice_id) -> list[dict]`.

- [ ] **Step 1: Create `backend/app/models.py`**

```python
from typing import TypedDict


class Chunk(TypedDict):
    text: str
    page: int | None
    chunk_index: int
    document_id: str
    title: str
    doc_type: str


class DocumentSummary(TypedDict):
    document_id: str
    status: str
    n_chunks: int
```

- [ ] **Step 2: Create `backend/app/schema.sql`** (full DDL from Blueprint §5.2)

```sql
-- ====== Tenant / práctica ======
CREATE TABLE IF NOT EXISTS practices (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    type         TEXT NOT NULL CHECK (type IN ('clinica','odontologia','psicologia','tutoria','legal','otro')),
    settings     JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id  UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    full_name    TEXT NOT NULL,
    email        TEXT UNIQUE NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('admin','profesional','owner')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS practitioners (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id   UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    user_id       UUID REFERENCES users(id),
    full_name     TEXT NOT NULL,
    speciality    TEXT,
    working_hours JSONB DEFAULT '{}'::jsonb,
    active        BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS clients (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id  UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    full_name    TEXT NOT NULL,
    dob          DATE,
    email        TEXT,
    phone        TEXT,
    tags         JSONB DEFAULT '[]'::jsonb,
    status       TEXT NOT NULL DEFAULT 'activo' CHECK (status IN ('activo','inactivo','baja')),
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_clients_practice ON clients(practice_id);

CREATE TABLE IF NOT EXISTS documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    practice_id  UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
    client_id    UUID REFERENCES clients(id),
    uploaded_by  UUID REFERENCES users(id),
    doc_type     TEXT NOT NULL,
    title        TEXT NOT NULL,
    file_uri     TEXT NOT NULL,
    mime_type    TEXT,
    page_count   INT,
    status       TEXT NOT NULL DEFAULT 'procesando'
                 CHECK (status IN ('procesando','indexado','error')),
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client_id);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(practice_id, doc_type);
```

> Nota: el slice solo usa `practices` + `documents`; las demás tablas se crean porque las FK de `documents` las requieren y porque el esquema es la fuente de verdad. Las tablas restantes del Blueprint (appointments, interactions, etc.) se agregan cuando Fase 1 las necesite.

- [ ] **Step 3: Create `backend/app/seed.sql`**

```sql
INSERT INTO practices (id, name, type)
VALUES ('00000000-0000-0000-0000-000000000001', 'Práctica Demo', 'psicologia')
ON CONFLICT (id) DO NOTHING;
```

- [ ] **Step 4: Apply schema + seed**

```bash
docker compose exec -T postgres psql -U praxia -d praxia < backend/app/schema.sql
docker compose exec -T postgres psql -U praxia -d praxia < backend/app/seed.sql
```

Expected: `CREATE TABLE` / `INSERT 0 1` (or `0 0` on re-run) without errors.

- [ ] **Step 5: Write the failing test** — `backend/tests/test_db.py`

```python
import os
import uuid

import pytest

from app import db


@pytest.mark.integration
async def test_insert_and_status_roundtrip():
    practice_id = os.environ["PRACTICE_ID"]
    doc_id = await db.insert_document(
        practice_id, doc_type="protocolo", title="T-" + uuid.uuid4().hex,
        file_uri="mem://x", mime_type="text/markdown",
    )
    await db.set_document_status(doc_id, "indexado", page_count=1)
    docs = await db.list_documents(practice_id)
    match = [d for d in docs if d["id"] == doc_id]
    assert match and match[0]["status"] == "indexado"
    assert match[0]["page_count"] == 1
```

- [ ] **Step 6: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_db.py -m integration -v`
Expected: FAIL (`AttributeError`/`ModuleNotFoundError` for `app.db`).

- [ ] **Step 7: Implement `backend/app/db.py`**

```python
from typing import Any

import asyncpg

from app.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_settings().database_url)
    return _pool


async def insert_document(
    practice_id: str, doc_type: str, title: str, file_uri: str, mime_type: str
) -> str:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO documents (practice_id, doc_type, title, file_uri, mime_type, status)
        VALUES ($1, $2, $3, $4, $5, 'procesando')
        RETURNING id
        """,
        practice_id, doc_type, title, file_uri, mime_type,
    )
    return str(row["id"])


async def set_document_status(
    document_id: str, status: str, page_count: int | None = None
) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE documents SET status = $2, page_count = $3 WHERE id = $1",
        document_id, status, page_count,
    )


async def list_documents(practice_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, title, doc_type, status, page_count, ingested_at
        FROM documents WHERE practice_id = $1 ORDER BY ingested_at DESC
        """,
        practice_id,
    )
    return [dict(r) for r in rows]
```

- [ ] **Step 8: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_db.py -m integration -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models.py backend/app/schema.sql backend/app/seed.sql backend/app/db.py backend/tests/test_db.py
git commit -m "feat: esquema, seed y capa de acceso a documents"
```

---

## Task 4: Embeddings (`embeddings.py`)

**Files:**
- Create: `backend/app/embeddings.py`
- Test: `backend/tests/test_embeddings.py`

**Interfaces:**
- Consumes: `get_settings()` (for `embed_model`, `embed_dim`).
- Produces: `async def embed_texts(texts: list[str]) -> list[list[float]]`, `async def embed_query(text: str) -> list[float]`. Vectors are L2-normalized, length 1024.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_embeddings.py`

```python
import pytest

from app import embeddings


@pytest.mark.integration  # downloads bge-m3 on first run
async def test_embed_dim_and_normalized():
    vecs = await embeddings.embed_texts(["hola mundo", "otro texto"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024
    norm = sum(x * x for x in vecs[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_embeddings.py -m integration -v`
Expected: FAIL (`ModuleNotFoundError: app.embeddings`).

- [ ] **Step 3: Implement `backend/app/embeddings.py`**

```python
import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embed_model)


def _encode(texts: list[str]) -> list[list[float]]:
    arr = _model().encode(texts, normalize_embeddings=True)
    return [row.tolist() for row in arr]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(_encode, texts)


async def embed_query(text: str) -> list[float]:
    vecs = await asyncio.to_thread(_encode, [text])
    return vecs[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_embeddings.py -m integration -v`
Expected: PASS (first run downloads the model — may take minutes).

- [ ] **Step 5: Commit**

```bash
git add backend/app/embeddings.py backend/tests/test_embeddings.py
git commit -m "feat: embeddings bge-m3 (singleton, normalizado, 1024d)"
```

---

## Task 5: Vector store (`vectorstore.py`)

**Files:**
- Create: `backend/app/vectorstore.py`
- Test: `backend/tests/test_vectorstore.py`

**Interfaces:**
- Consumes: `get_settings()`, `models.Chunk`.
- Produces:
  - `async def ensure_collection() -> None` (idempotent, size=1024, COSINE)
  - `async def upsert_chunks(chunks: list[Chunk], vectors: list[list[float]], practice_id: str) -> None`
  - `async def search(vector: list[float], practice_id: str, top_k: int) -> list[Chunk]`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_vectorstore.py`

```python
import pytest

from app import vectorstore
from app.models import Chunk


def _chunk(text: str, idx: int, doc: str) -> Chunk:
    return Chunk(text=text, page=1, chunk_index=idx, document_id=doc,
                 title="Protocolo", doc_type="protocolo")


@pytest.mark.integration
async def test_search_filters_by_practice():
    await vectorstore.ensure_collection()
    v_a = [1.0] + [0.0] * 1023
    v_b = [0.0, 1.0] + [0.0] * 1022
    await vectorstore.upsert_chunks([_chunk("texto practica A", 0, "doc-a")], [v_a], "practice-A")
    await vectorstore.upsert_chunks([_chunk("texto practica B", 0, "doc-b")], [v_b], "practice-B")

    hits = await vectorstore.search(v_a, practice_id="practice-A", top_k=5)
    assert hits, "should retrieve A's chunk"
    assert all(h["document_id"] != "doc-b" for h in hits), "must not leak practice B"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_vectorstore.py -m integration -v`
Expected: FAIL (`ModuleNotFoundError: app.vectorstore`).

- [ ] **Step 3: Implement `backend/app/vectorstore.py`**

```python
import uuid

from qdrant_client import AsyncQdrantClient, models

from app.config import get_settings
from app.models import Chunk

_client: AsyncQdrantClient | None = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=get_settings().qdrant_url)
    return _client


async def ensure_collection() -> None:
    s = get_settings()
    client = _get_client()
    if not await client.collection_exists(s.qdrant_collection):
        await client.create_collection(
            collection_name=s.qdrant_collection,
            vectors_config=models.VectorParams(size=s.embed_dim, distance=models.Distance.COSINE),
        )


async def upsert_chunks(
    chunks: list[Chunk], vectors: list[list[float]], practice_id: str
) -> None:
    s = get_settings()
    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={**chunk, "practice_id": practice_id},
        )
        for chunk, vec in zip(chunks, vectors, strict=True)
    ]
    await _get_client().upsert(collection_name=s.qdrant_collection, points=points)


async def search(vector: list[float], practice_id: str, top_k: int) -> list[Chunk]:
    s = get_settings()
    result = await _get_client().query_points(
        collection_name=s.qdrant_collection,
        query=vector,
        query_filter=models.Filter(
            must=[models.FieldCondition(key="practice_id", match=models.MatchValue(value=practice_id))]
        ),
        limit=top_k,
        with_payload=True,
    )
    out: list[Chunk] = []
    for point in result.points:
        p = point.payload or {}
        out.append(Chunk(
            text=p["text"], page=p.get("page"), chunk_index=p["chunk_index"],
            document_id=p["document_id"], title=p["title"], doc_type=p["doc_type"],
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_vectorstore.py -m integration -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/vectorstore.py backend/tests/test_vectorstore.py
git commit -m "feat: vector store Qdrant con filtro por practice_id"
```

---

## Task 6: Document parsing (`ingest/parse.py`)

**Files:**
- Create: `backend/app/ingest/parse.py`
- Test: `backend/tests/test_parse.py`, `backend/tests/fixtures/protocolo.md`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ParsedDoc` (TypedDict: `pages: list[tuple[int | None, str]]`), `def parse(data: bytes, filename: str) -> ParsedDoc`. Raises `ValueError` on unsupported type or empty text. PDF → one `(page_no, text)` per page; MD/TXT → single `(None, text)`.

- [ ] **Step 1: Create fixture** — `backend/tests/fixtures/protocolo.md`

```markdown
# Protocolo de primera consulta

La primera consulta dura 60 minutos. Se solicita al paciente llegar 10 minutos antes
para completar la ficha de admisión. La cancelación debe avisarse con 24 horas de
anticipación; de lo contrario se cobra el 50% del valor de la sesión.
```

- [ ] **Step 2: Write the failing test** — `backend/tests/test_parse.py`

```python
from pathlib import Path

import pytest

from app.ingest.parse import parse

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_markdown():
    data = (FIXTURES / "protocolo.md").read_bytes()
    parsed = parse(data, "protocolo.md")
    assert len(parsed["pages"]) == 1
    page_no, text = parsed["pages"][0]
    assert page_no is None
    assert "primera consulta dura 60 minutos" in text


def test_parse_unsupported_raises():
    with pytest.raises(ValueError):
        parse(b"\x00\x01", "imagen.png")


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse(b"   ", "vacio.md")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_parse.py -v`
Expected: FAIL (`ModuleNotFoundError: app.ingest.parse`).

- [ ] **Step 4: Implement `backend/app/ingest/parse.py`**

```python
from io import BytesIO
from typing import TypedDict

from pypdf import PdfReader


class ParsedDoc(TypedDict):
    pages: list[tuple[int | None, str]]


def parse(data: bytes, filename: str) -> ParsedDoc:
    name = filename.lower()
    if name.endswith(".pdf"):
        pages = _parse_pdf(data)
    elif name.endswith((".md", ".markdown", ".txt")):
        pages = [(None, data.decode("utf-8", errors="replace"))]
    else:
        raise ValueError(f"Tipo de archivo no soportado: {filename}")

    if not any(text.strip() for _, text in pages):
        raise ValueError(f"El documento no contiene texto extraíble: {filename}")
    return ParsedDoc(pages=pages)


def _parse_pdf(data: bytes) -> list[tuple[int | None, str]]:
    reader = PdfReader(BytesIO(data))
    return [(i + 1, (page.extract_text() or "")) for i, page in enumerate(reader.pages)]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_parse.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingest/parse.py backend/tests/test_parse.py backend/tests/fixtures/protocolo.md
git commit -m "feat: parser de documentos (PDF por pagina, MD/TXT)"
```

---

## Task 7: Chunking (`ingest/chunk.py`)

**Files:**
- Create: `backend/app/ingest/chunk.py`
- Test: `backend/tests/test_chunk.py`

**Interfaces:**
- Consumes: `ParsedDoc` (from Task 6), `Chunk` (from Task 3), `get_settings()`.
- Produces: `def chunk(parsed: ParsedDoc, document_id: str, title: str, doc_type: str) -> list[Chunk]`. Pure function. Splits each page's text with overlap; `chunk_index` is global and monotonically increasing; each chunk carries its source page.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_chunk.py`

```python
from app.config import get_settings
from app.ingest.chunk import chunk
from app.ingest.parse import ParsedDoc


def test_chunk_assigns_global_index_and_page():
    get_settings.cache_clear()
    long_text = "oración. " * 400  # forces multiple chunks
    parsed = ParsedDoc(pages=[(1, long_text), (2, "página dos corta.")])
    chunks = chunk(parsed, document_id="doc-1", title="Protocolo", doc_type="protocolo")

    assert len(chunks) >= 2
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert chunks[-1]["page"] == 2
    assert all(c["document_id"] == "doc-1" for c in chunks)


def test_chunk_empty_pages_yields_nothing():
    parsed = ParsedDoc(pages=[(1, "   ")])
    assert chunk(parsed, document_id="d", title="t", doc_type="protocolo") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_chunk.py -v`
Expected: FAIL (`ModuleNotFoundError: app.ingest.chunk`).

- [ ] **Step 3: Implement `backend/app/ingest/chunk.py`**

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings
from app.ingest.parse import ParsedDoc
from app.models import Chunk


def chunk(parsed: ParsedDoc, document_id: str, title: str, doc_type: str) -> list[Chunk]:
    s = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=s.chunk_size, chunk_overlap=s.chunk_overlap
    )
    out: list[Chunk] = []
    idx = 0
    for page_no, text in parsed["pages"]:
        for piece in splitter.split_text(text):
            if not piece.strip():
                continue
            out.append(Chunk(
                text=piece, page=page_no, chunk_index=idx,
                document_id=document_id, title=title, doc_type=doc_type,
            ))
            idx += 1
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_chunk.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest/chunk.py backend/tests/test_chunk.py
git commit -m "feat: chunking con overlap preservando pagina"
```

---

## Task 8: Ingest pipeline (`ingest/pipeline.py`)

**Files:**
- Create: `backend/app/ingest/pipeline.py`
- Test: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `db.insert_document/set_document_status`, `parse`, `chunk`, `embeddings.embed_texts`, `vectorstore.ensure_collection/upsert_chunks`, `get_settings`.
- Produces: `async def ingest_document(data: bytes, filename: str, doc_type: str, title: str) -> DocumentSummary`. Inserts the row (`procesando`), parses/chunks/embeds/upserts, marks `indexado` with `page_count`; on any failure marks `error` and re-raises.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_pipeline.py`

```python
import os
from pathlib import Path

import pytest

from app import db, vectorstore
from app.ingest.pipeline import ingest_document

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.integration
async def test_ingest_markdown_indexes():
    await vectorstore.ensure_collection()
    data = (FIXTURES / "protocolo.md").read_bytes()
    summary = await ingest_document(data, "protocolo.md", "protocolo", "Protocolo de primera consulta")

    assert summary["status"] == "indexado"
    assert summary["n_chunks"] >= 1
    docs = await db.list_documents(os.environ["PRACTICE_ID"])
    assert any(d["id"] == summary["document_id"] and d["status"] == "indexado" for d in docs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_pipeline.py -m integration -v`
Expected: FAIL (`ModuleNotFoundError: app.ingest.pipeline`).

- [ ] **Step 3: Implement `backend/app/ingest/pipeline.py`**

```python
from app import db, embeddings, vectorstore
from app.config import get_settings
from app.ingest.chunk import chunk
from app.ingest.parse import parse
from app.models import DocumentSummary


async def ingest_document(
    data: bytes, filename: str, doc_type: str, title: str
) -> DocumentSummary:
    s = get_settings()
    document_id = await db.insert_document(
        s.practice_id, doc_type=doc_type, title=title,
        file_uri=f"upload://{filename}", mime_type=_mime(filename),
    )
    try:
        parsed = parse(data, filename)
        chunks = chunk(parsed, document_id=document_id, title=title, doc_type=doc_type)
        if not chunks:
            raise ValueError("El documento no produjo chunks")
        vectors = await embeddings.embed_texts([c["text"] for c in chunks])
        await vectorstore.ensure_collection()
        await vectorstore.upsert_chunks(chunks, vectors, s.practice_id)
        page_count = len(parsed["pages"])
        await db.set_document_status(document_id, "indexado", page_count=page_count)
        return DocumentSummary(document_id=document_id, status="indexado", n_chunks=len(chunks))
    except Exception:
        await db.set_document_status(document_id, "error")
        raise


def _mime(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    return "text/markdown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_pipeline.py -m integration -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: pipeline de ingesta (parse->chunk->embed->qdrant+pg)"
```

---

## Task 9: Retrieval (`rag/retrieve.py`)

**Files:**
- Create: `backend/app/rag/retrieve.py`
- Test: `backend/tests/test_retrieve.py`

**Interfaces:**
- Consumes: `embeddings.embed_query`, `vectorstore.search`, `get_settings`.
- Produces: `async def retrieve(query: str, practice_id: str | None = None, top_k: int | None = None) -> list[Chunk]`. Defaults `practice_id`/`top_k` from settings.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_retrieve.py`

```python
import os
from pathlib import Path

import pytest

from app import vectorstore
from app.ingest.pipeline import ingest_document
from app.rag.retrieve import retrieve

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.integration
async def test_retrieve_finds_relevant_chunk():
    await vectorstore.ensure_collection()
    await ingest_document(
        (FIXTURES / "protocolo.md").read_bytes(), "protocolo.md",
        "protocolo", "Protocolo de primera consulta",
    )
    hits = await retrieve("¿cuánto dura la primera consulta?", practice_id=os.environ["PRACTICE_ID"])
    assert hits
    assert any("60 minutos" in h["text"] for h in hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_retrieve.py -m integration -v`
Expected: FAIL (`ModuleNotFoundError: app.rag.retrieve`).

- [ ] **Step 3: Implement `backend/app/rag/retrieve.py`**

```python
from app import embeddings, vectorstore
from app.config import get_settings
from app.models import Chunk


async def retrieve(
    query: str, practice_id: str | None = None, top_k: int | None = None
) -> list[Chunk]:
    s = get_settings()
    vector = await embeddings.embed_query(query)
    return await vectorstore.search(
        vector,
        practice_id=practice_id or s.practice_id,
        top_k=top_k or s.top_k,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_retrieve.py -m integration -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/retrieve.py backend/tests/test_retrieve.py
git commit -m "feat: retrieval por coseno filtrado por practice_id"
```

---

## Task 10: Synthesis with citations + abstention (`rag/synthesize.py`)

**Files:**
- Create: `backend/app/rag/synthesize.py`
- Test: `backend/tests/test_synthesize.py`

**Interfaces:**
- Consumes: `models.Chunk`, `get_settings`, `langchain_ollama.ChatOllama`.
- Produces:
  - `ABSTAIN_MESSAGE: str` (constant)
  - `def build_sources(chunks: list[Chunk]) -> list[dict]` → `[{"n", "title", "page", "document_id"}]`
  - `async def synthesize_stream(query: str, chunks: list[Chunk], llm=None) -> AsyncIterator[str]`. If `chunks` is empty → yields `ABSTAIN_MESSAGE` and returns (no LLM call). Otherwise streams `llm.astream(messages)` content. `llm` is injectable for testing; default is built from settings.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_synthesize.py`

```python
import pytest

from app.models import Chunk
from app.rag import synthesize


class FakeChunkMsg:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    async def astream(self, messages):
        # Echo a deterministic answer that cites source [1].
        for token in ["Según ", "el ", "protocolo ", "[1]."]:
            yield FakeChunkMsg(token)


def _chunk() -> Chunk:
    return Chunk(text="La primera consulta dura 60 minutos.", page=2, chunk_index=0,
                 document_id="doc-1", title="Protocolo", doc_type="protocolo")


async def test_abstains_without_context():
    out = "".join([t async for t in synthesize.synthesize_stream("hola", [])])
    assert out == synthesize.ABSTAIN_MESSAGE


async def test_streams_and_cites_with_context():
    out = "".join([
        t async for t in synthesize.synthesize_stream("¿cuánto dura?", [_chunk()], llm=FakeLLM())
    ])
    assert "[1]" in out


def test_build_sources_numbering():
    sources = synthesize.build_sources([_chunk()])
    assert sources == [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_synthesize.py -v`
Expected: FAIL (`ModuleNotFoundError: app.rag.synthesize`).

- [ ] **Step 3: Implement `backend/app/rag/synthesize.py`**

```python
from collections.abc import AsyncIterator
from typing import Any

from app.config import get_settings
from app.models import Chunk

ABSTAIN_MESSAGE = "No encuentro esa información en los documentos disponibles."

SYSTEM_PROMPT = (
    "Sos el asistente de una práctica profesional. Respondé en español SOLO con la "
    "información de los fragmentos provistos. Citá las fuentes que uses con la marca [n]. "
    "Si la respuesta no está en los fragmentos, respondé exactamente: "
    f"'{ABSTAIN_MESSAGE}'. No inventes ni uses conocimiento externo."
)


def _format_context(chunks: list[Chunk]) -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        page = f" — p.{c['page']}" if c["page"] is not None else ""
        blocks.append(f'[{i}] (Fuente: "{c["title"]}"{page})\n{c["text"]}')
    return "\n\n".join(blocks)


def build_sources(chunks: list[Chunk]) -> list[dict[str, Any]]:
    return [
        {"n": i, "title": c["title"], "page": c["page"], "document_id": c["document_id"]}
        for i, c in enumerate(chunks, start=1)
    ]


def _default_llm() -> Any:
    from langchain_ollama import ChatOllama

    s = get_settings()
    return ChatOllama(model=s.ollama_model, base_url=s.ollama_base_url, temperature=0.1)


async def synthesize_stream(
    query: str, chunks: list[Chunk], llm: Any = None
) -> AsyncIterator[str]:
    if not chunks:
        yield ABSTAIN_MESSAGE
        return
    llm = llm or _default_llm()
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", f"Fragmentos:\n\n{_format_context(chunks)}\n\nPregunta: {query}"),
    ]
    async for piece in llm.astream(messages):
        text = getattr(piece, "content", "")
        if text:
            yield text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_synthesize.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/synthesize.py backend/tests/test_synthesize.py
git commit -m "feat: sintesis con citas y abstencion (LLM inyectable)"
```

---

## Task 11: FastAPI app (`main.py`) — endpoints + SSE

**Files:**
- Create: `backend/app/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `ingest_document`, `retrieve`, `synthesize_stream`, `build_sources`, `db.list_documents`, `vectorstore.ensure_collection`, `get_settings`.
- Produces: FastAPI `app` with:
  - `GET /health` → `{"status": "ok"}`
  - `POST /ingest` (multipart `file`, form `doc_type`, `title`) → `DocumentSummary` JSON; 415 on unsupported, 422 on parse error
  - `POST /chat` (JSON `{"message": str}`) → SSE stream: `event: token` deltas, then `event: sources` (JSON), then `event: done`
  - `GET /documents` → list for the configured practice

- [ ] **Step 1: Write the failing test** — `backend/tests/test_api.py`

```python
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.rag import synthesize


class FakeChunkMsg:
    def __init__(self, content: str):
        self.content = content


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    class FakeLLM:
        async def astream(self, messages):
            for token in ["Respuesta ", "citada ", "[1]."]:
                yield FakeChunkMsg(token)

    monkeypatch.setattr(synthesize, "_default_llm", lambda: FakeLLM())


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health():
    async with await _client() as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.integration
async def test_ingest_then_chat_streams_sources():
    md = b"# Protocolo\nLa primera consulta dura 60 minutos."
    async with await _client() as c:
        ing = await c.post(
            "/ingest",
            files={"file": ("protocolo.md", md, "text/markdown")},
            data={"doc_type": "protocolo", "title": "Protocolo"},
        )
        assert ing.status_code == 200 and ing.json()["status"] == "indexado"

        async with c.stream("POST", "/chat", json={"message": "¿cuánto dura la consulta?"}) as resp:
            body = ""
            async for line in resp.aiter_lines():
                body += line + "\n"
    assert "event: token" in body
    assert "event: sources" in body


async def test_ingest_unsupported_type():
    async with await _client() as c:
        resp = await c.post(
            "/ingest",
            files={"file": ("foto.png", b"\x89PNG", "image/png")},
            data={"doc_type": "protocolo", "title": "Foto"},
        )
    assert resp.status_code == 415
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_api.py -v`
Expected: FAIL (`ModuleNotFoundError: app.main`).

- [ ] **Step 3: Implement `backend/app/main.py`**

```python
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import db, vectorstore
from app.config import get_settings
from app.ingest.pipeline import ingest_document
from app.rag.retrieve import retrieve
from app.rag.synthesize import build_sources, synthesize_stream

app = FastAPI(title="Praxia · Fase 0")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

SUPPORTED_SUFFIXES = (".pdf", ".md", ".markdown", ".txt")


@app.on_event("startup")
async def _startup() -> None:
    await vectorstore.ensure_collection()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    doc_type: str = Form("protocolo"),
    title: str = Form(...),
) -> dict:
    filename = file.filename or "documento"
    if not filename.lower().endswith(SUPPORTED_SUFFIXES):
        raise HTTPException(status_code=415, detail=f"Tipo no soportado: {filename}")
    data = await file.read()
    try:
        return dict(await ingest_document(data, filename, doc_type, title))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/documents")
async def documents() -> list[dict]:
    return await db.list_documents(get_settings().practice_id)


class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    chunks = await retrieve(req.message)

    async def event_stream() -> AsyncIterator[dict]:
        async for token in synthesize_stream(req.message, chunks):
            yield {"event": "token", "data": token}
        yield {"event": "sources", "data": json.dumps(build_sources(chunks), ensure_ascii=False)}
        yield {"event": "done", "data": "[DONE]"}

    return EventSourceResponse(event_stream())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_api.py -v`
Expected: PASS. (`test_health` and `test_ingest_unsupported_type` run without infra; `test_ingest_then_chat_streams_sources` needs Postgres+Qdrant.)

- [ ] **Step 5: Run full gate (lint, types, all non-LLM tests)**

```bash
backend/.venv/Scripts/python -m ruff check backend
backend/.venv/Scripts/python -m ruff format --check backend
backend/.venv/Scripts/python -m mypy backend/app
backend/.venv/Scripts/python -m pytest backend/tests -q
```
Expected: all green. Fix any ruff/mypy findings before committing.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat: API FastAPI (health, ingest, chat SSE, documents)"
```

---

## Task 12: End-to-end acceptance with the real LLM (manual + opt-in test)

**Files:**
- Create: `backend/tests/test_e2e_llm.py`

**Interfaces:**
- Consumes: the full stack + a running Ollama with `OLLAMA_MODEL` pulled (Tarea 0 del spec).
- Produces: an opt-in `@pytest.mark.llm` test plus a documented manual smoke matching the spec's Definition of Done.

- [ ] **Step 1: Verify Ollama is ready (Tarea 0)**

```bash
ollama --version          # must be >= 0.20.2
ollama pull gemma4:12b    # if the tag is missing: `ollama list` and set OLLAMA_MODEL in .env to an available gemma tag
ollama list
```
If `gemma4:12b` does not exist, pick the best available gemma model, update `OLLAMA_MODEL` in `.env`, and continue — no code change needed.

- [ ] **Step 2: Write the opt-in LLM test** — `backend/tests/test_e2e_llm.py`

```python
from httpx import ASGITransport, AsyncClient
import pytest

from app.main import app


@pytest.mark.llm
@pytest.mark.integration
async def test_real_llm_answers_with_citation():
    md = b"# Protocolo\nLa primera consulta dura 60 minutos y se cobra 50% por cancelacion tardia."
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/ingest",
            files={"file": ("protocolo.md", md, "text/markdown")},
            data={"doc_type": "protocolo", "title": "Protocolo"},
        )
        async with c.stream("POST", "/chat", json={"message": "¿cuánto dura la primera consulta?"}) as resp:
            body = "".join([line async for line in resp.aiter_lines()])
    assert "60" in body
    assert "event: sources" in body
```

- [ ] **Step 3: Run the opt-in LLM test**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_e2e_llm.py -m llm -v`
Expected: PASS (requires Ollama running with the model). This may take 10-60s on first call.

- [ ] **Step 4: Manual smoke (spec DoD)**

```bash
backend/.venv/Scripts/python -m uvicorn app.main:app --reload --app-dir backend
```
Then, with the server up:
- `curl -F file=@backend/tests/fixtures/protocolo.md -F doc_type=protocolo -F title=Protocolo http://localhost:8000/ingest` → returns `status: indexado`.
- `curl -N -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"message":"¿cuánto dura la primera consulta?"}'` → streamed answer mentioning 60 minutos + a `sources` event.
- Ask something uncovered (`-d '{"message":"¿cuál es la dirección de la clínica?"}'`) → the abstention message.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_e2e_llm.py
git commit -m "test: smoke end-to-end con LLM real (opt-in)"
```

---

## Self-Review (author checklist — completed)

**Spec coverage:**
- DoD #1 infra → Task 1. DoD #2 schema+seed → Task 3. DoD #3 ingest→indexado → Tasks 6-8, 11. DoD #4 cited streamed answer → Tasks 10-12. DoD #5 abstention → Task 10 (`test_abstains_without_context`) + Task 12 manual. DoD #6 practice_id isolation → Task 5 (`test_search_filters_by_practice`). DoD #7 ruff/mypy/pytest gate → Task 11 Step 5. DoD #8 no external network → enforced by design (only Ollama/Qdrant/Postgres local).
- Spec §5 components: every backend module (`config, db, vectorstore, embeddings, parse, chunk, pipeline, retrieve, synthesize, main`) has a task. `models.py` folded into Task 3.
- Spec §12 Tarea 0 (Ollama de-risk) → Task 12 Step 1.

**Placeholder scan:** no TBD/TODO; every code step contains complete code and exact commands.

**Type consistency:** `Chunk` shape identical across Tasks 3/5/7/10. `synthesize_stream(query, chunks, llm=None)` signature matches its use in `main.py`. `build_sources` return shape matches `test_synthesize`. `_default_llm` is the monkeypatch seam used by `test_api`.

**Out of scope (deferred):** LangGraph, MCP, reranker/judges, Presidio, NL2SQL, write tools/HITL, memory, caching, docling/OCR — none appear here, per spec §14.
