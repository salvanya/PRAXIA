from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from app.agents.sql_agent import answer_structured
from app.agents.sql_present import synthesize_sql_answer
from app.config import get_settings
from app.graph.rag_subgraph import crag_app, initial_rag_state
from app.graph.state import AgentState, last_user_text

STUB_MESSAGE = "Esa función todavía no está disponible (próximo slice)."
SCOPE_MESSAGE = (
    "Solo puedo ayudarte con la información y los datos de tu práctica. "
    "¿Querés que busque algo en tus documentos o tu agenda?"
)
SQL_ABSTAIN_MESSAGE = (
    "No pude traducir tu pregunta a una consulta segura sobre tus datos. ¿Podés reformularla?"
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
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model, temperature=0.3)


def _stream_chunks(text: str, size: int = 24) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


async def rag_node(state: AgentState) -> dict:
    result = await crag_app.ainvoke(initial_rag_state(last_user_text(state), state["practice_id"]))
    answer = result["answer"]
    if result["abstained"]:
        write_token(answer)
        write_sources([])
        return {
            "retrieved": result["reranked"],
            "sources": [],
            "messages": [AIMessage(content=answer)],
        }
    for piece in _stream_chunks(answer):
        write_token(piece)
    write_sources(result["sources"])
    return {
        "retrieved": result["reranked"],
        "sources": result["sources"],
        "messages": [AIMessage(content=answer)],
    }


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


async def sql_node(state: AgentState) -> dict:
    result = await answer_structured(last_user_text(state), state["practice_id"])
    if result.abstained:
        write_token(SQL_ABSTAIN_MESSAGE)
        write_sources([])
        answer = SQL_ABSTAIN_MESSAGE
    else:
        answer = await synthesize_sql_answer(last_user_text(state), result.rows, result.columns)
        for piece in _stream_chunks(answer):
            write_token(piece)
        write_sources([])
    return {
        "sources": [],
        "candidate_sql": result.sql or "",
        "judge_scores": {"sql_match": not result.abstained},
        "messages": [AIMessage(content=answer)],
    }


async def action_stub(state: AgentState) -> dict:
    # TODO(write-slice): reemplazar el stub por interrupt + tarjeta de confirmación (CLAUDE.md §4).
    write_token(STUB_MESSAGE)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=STUB_MESSAGE)]}
