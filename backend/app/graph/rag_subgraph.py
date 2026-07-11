import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.models import Chunk
from app.rag.judges import judge_groundedness, judge_relevance
from app.rag.reformulate import reformulate
from app.rag.rerank import rerank
from app.rag.retrieve import retrieve
from app.rag.synthesize import ABSTAIN_MESSAGE, select_sources, synthesize

logger = logging.getLogger(__name__)


class RagState(TypedDict):
    original_query: str  # la pregunta del usuario (no muta)
    query: str  # query de búsqueda actual (puede reformularse)
    practice_id: str
    attempts: int  # intentos de retrieve consumidos
    reranked: list[Chunk]  # top_k tras rerank+floor del intento actual
    sufficient: bool  # veredicto del juez de relevancia
    answer: str  # borrador sintetizado (buffered)
    grounded: bool  # veredicto del juez de groundedness
    abstained: bool  # True si terminó en abstención
    sources: list[dict]  # build_sources(reranked) — solo se llena en éxito
    memories: list[dict]  # memorias practice-scope inyectadas en la síntesis


def initial_rag_state(query: str, practice_id: str, memories: list[dict] | None = None) -> RagState:
    return {
        "original_query": query,
        "query": query,
        "practice_id": practice_id,
        "attempts": 0,
        "reranked": [],
        "sufficient": False,
        "answer": "",
        "grounded": False,
        "abstained": False,
        "sources": [],
        "memories": memories or [],
    }


async def retrieve_node(state: RagState) -> dict[str, Any]:
    s = get_settings()
    candidates = await retrieve(
        state["query"], practice_id=state["practice_id"], top_k=s.rag_fetch_k
    )
    reranked = await rerank(state["query"], candidates)
    return {"reranked": reranked, "attempts": state["attempts"] + 1}


async def grade_node(state: RagState) -> dict[str, Any]:
    memories = state.get("memories", [])
    if not state["reranked"] and not memories:
        return {"sufficient": False}
    try:
        verdict = await judge_relevance(
            state["original_query"], state["reranked"], memories=memories
        )
        return {"sufficient": verdict.sufficient}
    except Exception:
        logger.warning("juez de relevancia falló; trato como insuficiente", exc_info=True)
        return {"sufficient": False}


def grade_router(state: RagState) -> str:
    if state["sufficient"]:
        return "synthesize"
    if state["attempts"] < get_settings().rag_max_attempts:
        return "reformulate"
    return "abstain"


async def reformulate_node(state: RagState) -> dict[str, Any]:
    new_query = await reformulate(state["original_query"], state["reranked"])
    return {"query": new_query}


async def synthesize_node(state: RagState) -> dict[str, Any]:
    answer = await synthesize(
        state["original_query"], state["reranked"], memories=state.get("memories", [])
    )
    return {"answer": answer}


def synth_router(state: RagState) -> str:
    answer = state["answer"].strip()
    if not answer or answer == ABSTAIN_MESSAGE:
        return "abstain"
    return "groundedness"


async def groundedness_node(state: RagState) -> dict[str, Any]:
    memories = state.get("memories", [])
    try:
        verdict = await judge_groundedness(state["answer"], state["reranked"], memories=memories)
        grounded = verdict.grounded
    except Exception:
        logger.warning("juez de groundedness falló; trato como no fundamentado", exc_info=True)
        grounded = False
    if grounded:
        return {
            "grounded": True,
            "abstained": False,
            "sources": select_sources(state["reranked"], state["answer"], memories),
        }
    return {"grounded": False}


def ground_router(state: RagState) -> str:
    return "finalize" if state["grounded"] else "abstain"


async def abstain_node(state: RagState) -> dict[str, Any]:
    return {"abstained": True, "answer": ABSTAIN_MESSAGE, "sources": []}


def build_crag() -> Any:
    g = StateGraph(RagState)
    g.add_node("retrieve", retrieve_node)
    g.add_node("grade", grade_node)
    g.add_node("reformulate", reformulate_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("groundedness", groundedness_node)
    g.add_node("abstain", abstain_node)

    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges(
        "grade",
        grade_router,
        {"synthesize": "synthesize", "reformulate": "reformulate", "abstain": "abstain"},
    )
    g.add_edge("reformulate", "retrieve")
    g.add_conditional_edges(
        "synthesize",
        synth_router,
        {"groundedness": "groundedness", "abstain": "abstain"},
    )
    g.add_conditional_edges(
        "groundedness",
        ground_router,
        {"finalize": END, "abstain": "abstain"},
    )
    g.add_edge("abstain", END)
    return g.compile()


crag_app = build_crag()
