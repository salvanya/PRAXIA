from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from app.agents.action_agent import propose_appointment
from app.agents.sql_agent import answer_structured
from app.agents.sql_present import synthesize_sql_answer
from app.config import get_settings
from app.db import create_appointment
from app.graph.rag_subgraph import crag_app, initial_rag_state
from app.graph.state import AgentState, last_user_text

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


def _format_receipt(params: dict, row: dict) -> str:
    start = datetime.fromisoformat(params["start_at"])
    return (
        f"✅ Turno creado: {params['client_name']} con {params['practitioner_name']} "
        f"el {start.strftime('%d/%m %H:%M')} (estado: {row['status']})."
    )


async def propose_appointment_node(state: AgentState) -> dict:
    result = await propose_appointment(
        last_user_text(state), state["practice_id"], now=datetime.now(UTC)
    )
    if result.abstained:
        write_token(result.message)
        write_sources([])
        return {
            "proposed_action": None,
            "sources": [],
            "messages": [AIMessage(content=result.message)],
        }
    return {"proposed_action": result.proposed_action}


async def confirm_appointment_node(state: AgentState) -> dict:
    action = state["proposed_action"] or {}
    decision = interrupt(action)
    if decision == "confirm":
        params = action["params"]
        row = await create_appointment(
            state["practice_id"],
            params["client_id"],
            params["practitioner_id"],
            datetime.fromisoformat(params["start_at"]),
            datetime.fromisoformat(params["end_at"]),
            reason=params.get("reason"),
            channel=params.get("channel"),
            status=params.get("status", "programado"),
        )
        msg = _format_receipt(params, row)
    else:
        msg = "Cancelado, no creé el turno."
    write_token(msg)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=msg)]}
