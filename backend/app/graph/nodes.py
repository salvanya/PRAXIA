from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from app.agents.choice_agent import resolve_choice
from app.agents.resolvers import _format_candidate
from app.agents.sql_agent import answer_structured
from app.agents.sql_present import synthesize_sql_answer
from app.agents.write_tools import REGISTRY, classify_write_action
from app.config import get_settings
from app.context import format_memories_block
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


def write_table(columns: list[str], rows: list[dict], sql: str) -> None:
    get_stream_writer()({"kind": "table", "columns": columns, "rows": rows, "sql": sql})


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


def _history_messages(state: AgentState, window: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    # window=0 significa "sin historial" — [-0:] sería lista completa (bug silencioso).
    tail = state["messages"][-window:] if window > 0 else []
    for m in tail:
        text = getattr(m, "content", "")
        if not isinstance(text, str) or not text:
            continue
        out.append(("human" if isinstance(m, HumanMessage) else "ai", text))
    return out


async def chitchat_node(state: AgentState) -> dict:
    llm = _chitchat_llm()
    window = get_settings().short_term_history_window
    block = format_memories_block(state.get("memories", []))
    mem = [("system", block)] if block else []
    messages = [("system", CHITCHAT_SYSTEM), *mem, *_history_messages(state, window)]
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
        answer = await synthesize_sql_answer(
            last_user_text(state), result.rows, result.columns, memories=state.get("memories", [])
        )
        for piece in _stream_chunks(answer):
            write_token(piece)
        is_tabular = bool(result.rows) and not (len(result.rows) == 1 and len(result.columns) == 1)
        if is_tabular:
            write_table(result.columns, result.rows, result.sql or "")
        write_sources([])
    return {
        "sources": [],
        "candidate_sql": result.sql or "",
        "judge_scores": {"sql_match": not result.abstained},
        "messages": [AIMessage(content=answer)],
    }


def _numbered(candidates: list[dict], stage: str) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        label = c["full_name"] if stage == "client" else _format_candidate(c)
        lines.append(f"{i}. {label}")
    return "\n".join(lines)


def _handle_proposal_result(result: Any, *, kind: str, question: str, overrides: dict) -> dict:
    if result.clarification is not None:
        clar = result.clarification
        pending = {
            "kind": kind,
            "stage": clar.stage,
            "candidates": clar.candidates,
            "question": question,
            "overrides": overrides,
        }
        numbered = _numbered(clar.candidates, clar.stage)
        msg = f"{clar.prompt}:\n{numbered}\n¿Cuál? Respondé con el número."
        write_token(msg)
        write_sources([])
        return {
            "pending_clarification": pending,
            "proposed_action": None,
            "sources": [],
            "messages": [AIMessage(content=msg)],
        }
    if result.abstained:
        write_token(result.message)
        write_sources([])
        return {
            "pending_clarification": None,
            "proposed_action": None,
            "sources": [],
            "messages": [AIMessage(content=result.message)],
        }
    return {"pending_clarification": None, "proposed_action": result.proposed_action}


async def clarify_node(state: AgentState) -> dict:
    pending = state["pending_clarification"]
    assert pending is not None  # entry_route garantiza no-None acá
    candidates = pending["candidates"]
    reply = last_user_text(state)
    idx = await resolve_choice(_numbered(candidates, pending["stage"]), reply, n=len(candidates))
    if not 1 <= idx <= len(candidates):
        msg = "No identifiqué cuál; volvé a pedírmelo indicando la fecha o el nombre completo."
        write_token(msg)
        write_sources([])
        return {"pending_clarification": None, "sources": [], "messages": [AIMessage(content=msg)]}
    overrides = {**pending["overrides"], pending["stage"]: candidates[idx - 1]}
    result = await REGISTRY[pending["kind"]].propose(
        pending["question"],
        state["practice_id"],
        now=datetime.now(UTC),
        client_override=overrides.get("client"),
        appointment_override=overrides.get("appointment"),
    )
    return _handle_proposal_result(
        result, kind=pending["kind"], question=pending["question"], overrides=overrides
    )


async def propose_action_node(state: AgentState) -> dict:
    question = last_user_text(state)
    try:
        kind = await classify_write_action(question)
    except Exception:  # noqa: BLE001 - fail-closed: si el clasificador falla, no adivinamos
        kind = "unsupported"
    if kind not in REGISTRY:
        msg = (
            "Por ahora puedo agendar turnos, reprogramar o cancelar turnos, "
            "registrar interacciones o actualizar datos de clientes "
            "(teléfono, email, estado). ¿Qué necesitás?"
        )
        write_token(msg)
        write_sources([])
        return {
            "proposed_action": None,
            "pending_clarification": None,
            "sources": [],
            "messages": [AIMessage(content=msg)],
        }
    result = await REGISTRY[kind].propose(question, state["practice_id"], now=datetime.now(UTC))
    return _handle_proposal_result(result, kind=kind, question=question, overrides={})


async def confirm_action_node(state: AgentState) -> dict:
    action = state["proposed_action"]
    assert action is not None  # route_after_propose garantiza no-None acá
    tool = REGISTRY[action["kind"]]
    decision = interrupt(action)
    if decision == "confirm":
        row = await tool.write(state["practice_id"], action["params"])
        msg = tool.format_receipt(action["params"], row)
    else:
        msg = tool.cancel_message
    write_token(msg)
    write_sources([])
    return {"sources": [], "proposed_action": None, "messages": [AIMessage(content=msg)]}
