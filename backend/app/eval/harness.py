from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.config import get_settings
from app.eval.cases import CaseResult, EvalCase
from app.graph.build import get_default_graph
from app.graph.state import new_state


def _last_ai_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


async def run_case(case: EvalCase, graph: Any = None) -> CaseResult:
    graph = graph or get_default_graph()
    state = await graph.ainvoke(new_state(case.question, get_settings().practice_id, uuid4().hex))
    return CaseResult(
        case=case,
        intent=state.get("intent", ""),
        answer=_last_ai_text(state.get("messages", [])),
        retrieved=state.get("retrieved", []),
        sources=state.get("sources", []),
        candidate_sql=state.get("candidate_sql", ""),
    )
