from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.edges import route
from app.graph.nodes import (
    action_stub,
    chitchat_node,
    rag_node,
    scope_reject_node,
    sql_stub,
)
from app.graph.router import router_node
from app.graph.state import AgentState

_LEAF_NODES = ("rag", "chitchat", "scope_reject", "sql_stub", "action_stub")


def build_graph(checkpointer: Any = None) -> Any:
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("rag", rag_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("scope_reject", scope_reject_node)
    g.add_node("sql_stub", sql_stub)
    g.add_node("action_stub", action_stub)

    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router",
        route,
        {
            "rag": "rag",
            "chitchat": "chitchat",
            "scope_reject": "scope_reject",
            "sql_stub": "sql_stub",
            "action_stub": "action_stub",
        },
    )
    for node in _LEAF_NODES:
        g.add_edge(node, END)

    return g.compile(checkpointer=checkpointer)


@lru_cache
def get_default_graph() -> Any:
    """Grafo sin checkpointer (tests / fallback cuando el lifespan no corrió)."""
    return build_graph(checkpointer=None)
