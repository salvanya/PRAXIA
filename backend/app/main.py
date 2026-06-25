import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import db, vectorstore
from app.config import get_settings
from app.graph.build import build_graph, get_default_graph
from app.graph.state import new_state
from app.ingest.pipeline import ingest_document
from app.rag.synthesize import ollama_available

SUPPORTED_SUFFIXES = (".pdf", ".md", ".markdown", ".txt")


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    await vectorstore.ensure_collection()
    s = get_settings()
    # Import diferido: la recolección de tests (httpx ASGITransport) no corre el
    # lifespan, y así no exige psycopg/Postgres para importar el módulo.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(s.database_url) as saver:
        await saver.setup()
        app_.state.graph = build_graph(checkpointer=saver)
        yield


app = FastAPI(title="Praxia · Fase 1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),  # noqa: B008
    doc_type: str = Form("protocolo"),  # noqa: B008
    title: str = Form(...),  # noqa: B008
) -> dict[str, Any]:
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
async def chat(req: ChatRequest, request: Request) -> EventSourceResponse:
    # El router e4b (y los nodos LLM) necesitan Ollama: si está caído, 503 amable
    # antes de abrir el stream SSE (preserva el fix de la limpieza pre-Fase 1).
    if not await ollama_available():
        raise HTTPException(
            status_code=503,
            detail="El asistente local (Ollama) no está disponible. "
            "Verificá que Ollama esté corriendo y volvé a intentar.",
        )

    graph = getattr(request.app.state, "graph", None) or get_default_graph()
    s = get_settings()
    state = new_state(req.message, practice_id=s.practice_id, thread_id=str(uuid4()))
    config = {"configurable": {"thread_id": state["thread_id"]}}

    async def event_stream() -> AsyncIterator[dict]:
        async for chunk in graph.astream(state, config, stream_mode="custom"):
            kind = chunk.get("kind")
            if kind == "token":
                yield {"event": "token", "data": chunk["text"]}
            elif kind == "sources":
                yield {
                    "event": "sources",
                    "data": json.dumps(chunk["sources"], ensure_ascii=False),
                }
        yield {"event": "done", "data": "[DONE]"}

    return EventSourceResponse(event_stream())
