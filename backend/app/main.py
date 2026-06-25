import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import db, vectorstore
from app.config import get_settings
from app.ingest.pipeline import ingest_document
from app.rag.retrieve import retrieve
from app.rag.synthesize import build_sources, ollama_available, synthesize_stream

SUPPORTED_SUFFIXES = (".pdf", ".md", ".markdown", ".txt")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await vectorstore.ensure_collection()
    yield


app = FastAPI(title="Praxia · Fase 0", lifespan=lifespan)
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
async def chat(req: ChatRequest) -> EventSourceResponse:
    chunks = await retrieve(req.message)
    if chunks and not await ollama_available():
        raise HTTPException(
            status_code=503,
            detail="El asistente local (Ollama) no está disponible. "
            "Verificá que Ollama esté corriendo y volvé a intentar.",
        )

    async def event_stream() -> AsyncIterator[dict]:
        async for token in synthesize_stream(req.message, chunks):
            yield {"event": "token", "data": token}
        yield {"event": "sources", "data": json.dumps(build_sources(chunks), ensure_ascii=False)}
        yield {"event": "done", "data": "[DONE]"}

    return EventSourceResponse(event_stream())
