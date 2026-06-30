from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph.message import add_messages

from app.models import Chunk


class AgentState(TypedDict):
    """State tipado y mínimo del grafo (CLAUDE.md §4).

    Campos declarados para slices posteriores (plan, candidate_sql,
    proposed_action, judge_scores, memories, running_summary) se agregarán
    cuando su slice los escriba; se mantiene el state chico a propósito.
    """

    messages: Annotated[list, add_messages]
    practice_id: str
    thread_id: str
    intent: str
    retrieved: list[Chunk]
    sources: list[dict]
    candidate_sql: str
    judge_scores: dict
    proposed_action: dict | None
    pending_clarification: dict | None


def new_state(message: str, practice_id: str, thread_id: str) -> AgentState:
    return {
        "messages": [HumanMessage(content=message)],
        "practice_id": practice_id,
        "thread_id": thread_id,
        "intent": "",
        "retrieved": [],
        "sources": [],
        "candidate_sql": "",
        "judge_scores": {},
        "proposed_action": None,
        "pending_clarification": None,
    }


def last_user_text(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""
