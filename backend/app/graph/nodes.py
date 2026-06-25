from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from app.config import get_settings
from app.graph.state import AgentState, last_user_text
from app.rag.retrieve import retrieve
from app.rag.synthesize import ABSTAIN_MESSAGE, build_sources, synthesize_stream

STUB_MESSAGE = "Esa función todavía no está disponible (próximo slice)."
SCOPE_MESSAGE = (
    "Solo puedo ayudarte con la información y los datos de tu práctica. "
    "¿Querés que busque algo en tus documentos o tu agenda?"
)

CHITCHAT_SYSTEM = (
    "Sos el asistente de una práctica profesional. Respondé saludos y charla trivial "
    "en español, breve y cordial. No inventes datos de la práctica."
)


def write_token(text: str) -> None:
    if text:
        get_stream_writer()({"kind": "token", "text": text})


def write_sources(sources: list[dict]) -> None:
    get_stream_writer()({"kind": "sources", "sources": sources})


def _chitchat_llm() -> Any:
    from langchain_ollama import ChatOllama

    s = get_settings()
    return ChatOllama(model=s.ollama_model, base_url=s.ollama_base_url, temperature=0.3)


async def rag_node(state: AgentState) -> dict:
    query = last_user_text(state)
    chunks = await retrieve(query, practice_id=state["practice_id"])
    if not chunks:
        write_token(ABSTAIN_MESSAGE)
        write_sources([])
        return {"retrieved": [], "sources": [], "messages": [AIMessage(content=ABSTAIN_MESSAGE)]}

    full = ""
    async for piece in synthesize_stream(query, chunks):
        write_token(piece)
        full += piece
    sources = build_sources(chunks)
    write_sources(sources)
    return {"retrieved": chunks, "sources": sources, "messages": [AIMessage(content=full)]}


async def chitchat_node(state: AgentState) -> dict:
    llm = _chitchat_llm()
    messages = [("system", CHITCHAT_SYSTEM), ("human", last_user_text(state))]
    full = ""
    async for piece in llm.astream(messages):
        text = getattr(piece, "content", "")
        if text:
            write_token(text)
            full += text
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=full)]}


async def scope_reject_node(state: AgentState) -> dict:
    write_token(SCOPE_MESSAGE)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=SCOPE_MESSAGE)]}


async def sql_stub(state: AgentState) -> dict:
    write_token(STUB_MESSAGE)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=STUB_MESSAGE)]}


async def action_stub(state: AgentState) -> dict:
    # TODO(write-slice): reemplazar el stub por interrupt + tarjeta de confirmación (CLAUDE.md §4).
    write_token(STUB_MESSAGE)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=STUB_MESSAGE)]}
