import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from langgraph.types import Command
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
    thread_id: str | None = None


class ResumeRequest(BaseModel):
    thread_id: str
    decision: Literal["confirm", "cancel"]


def select_chat_input(snapshot_values: dict, message: str, practice_id: str, thread_id: str) -> Any:
    """Primer turno (sin estado en el checkpoint) → state inicial completo; turno siguiente
    → parche incremental (solo el mensaje), para no pisar pending_clarification/proposed_action."""
    if snapshot_values:
        return {"messages": [HumanMessage(content=message)]}
    return new_state(message, practice_id=practice_id, thread_id=thread_id)


async def _sse_event_stream(graph: Any, inp: Any, config: dict) -> AsyncIterator[dict]:
    tid = config["configurable"]["thread_id"]
    async for mode, chunk in graph.astream(inp, config, stream_mode=["custom", "updates"]):
        if mode == "custom":
            kind = chunk.get("kind")
            if kind == "token":
                yield {"event": "token", "data": chunk["text"]}
            elif kind == "sources":
                yield {"event": "sources", "data": json.dumps(chunk["sources"], ensure_ascii=False)}
        elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupts = chunk["__interrupt__"]
            value = interrupts[0].value if interrupts else {}
            yield {
                "event": "confirm",
                "data": json.dumps({"thread_id": tid, "action": value}, ensure_ascii=False),
            }
    yield {"event": "done", "data": "[DONE]"}


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
    thread_id = req.thread_id or str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await graph.aget_state(config)
        values = snapshot.values
    except Exception:  # noqa: BLE001 - sin checkpointer (fallback get_default_graph) → arranca limpio
        values = {}
    inp = select_chat_input(values, req.message, s.practice_id, thread_id)
    return EventSourceResponse(_sse_event_stream(graph, inp, config))


@app.post("/chat/resume")
async def chat_resume(req: ResumeRequest, request: Request) -> EventSourceResponse:
    # El resume es determinístico (recibo/cancelación sin LLM) → no probamos Ollama.
    graph = getattr(request.app.state, "graph", None) or get_default_graph()
    config = {"configurable": {"thread_id": req.thread_id}}
    return EventSourceResponse(_sse_event_stream(graph, Command(resume=req.decision), config))
