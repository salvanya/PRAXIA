from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.eval.cases import EvalCase
from app.eval.harness import run_case


class _FakeGraph:
    def __init__(self, state: dict) -> None:
        self._state = state

    async def ainvoke(self, _input: dict) -> dict:
        return self._state


async def test_run_case_maps_state() -> None:
    case = EvalCase(
        question="¿cuánto dura la primera consulta?",
        category="rag",
        intent="rag",
        expected_behavior="cited_answer",
        must_include=["60"],
        ground_truth="dura 60",
    )
    state: dict[str, Any] = {
        "intent": "rag",
        "messages": [HumanMessage("q"), AIMessage("La primera consulta dura 60 minutos.")],
        "retrieved": [],
        "sources": [{"n": 1, "title": "protocolo", "page": None, "document_id": "d1"}],
        "candidate_sql": "",
    }
    result = await run_case(case, graph=_FakeGraph(state))
    assert result.intent == "rag"
    assert result.answer == "La primera consulta dura 60 minutos."
    assert result.sources[0]["title"] == "protocolo"
    assert result.case is case
