from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.edges import route, route_after_propose
from app.graph.nodes import (
    chitchat_node,
    confirm_action_node,
    propose_action_node,
    rag_node,
    scope_reject_node,
    sql_node,
)
from app.graph.router import router_node
from app.graph.state import AgentState

_LEAF_NODES = ("rag", "chitchat", "scope_reject", "sql_node", "confirm_action")


def build_graph(checkpointer: Any = None) -> Any:
    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    g.add_node("rag", rag_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("scope_reject", scope_reject_node)
    g.add_node("sql_node", sql_node)
    g.add_node("propose_action", propose_action_node)
    g.add_node("confirm_action", confirm_action_node)

    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router",
        route,
        {
            "rag": "rag",
            "chitchat": "chitchat",
            "scope_reject": "scope_reject",
            "sql_node": "sql_node",
            "propose_action": "propose_action",
        },
    )
    g.add_conditional_edges(
        "propose_action",
        route_after_propose,
        {"confirm_action": "confirm_action", END: END},
    )
    for node in _LEAF_NODES:
        g.add_edge(node, END)

    return g.compile(checkpointer=checkpointer)


@lru_cache
def get_default_graph() -> Any:
    """Grafo sin checkpointer (tests / fallback cuando el lifespan no corrió).
    Nota: el camino de escritura (interrupt) requiere checkpointer; en runtime
    real lo provee el lifespan (AsyncPostgresSaver)."""
    return build_graph(checkpointer=None)
