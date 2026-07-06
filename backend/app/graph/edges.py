from app.graph.state import AgentState

_INTENT_TO_NODE = {
    "rag": "rag",
    "sql": "sql_node",
    "action": "propose_action",
    "chitchat": "chitchat",
    "out_of_scope": "scope_reject",
}


def route(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state["intent"], "scope_reject")


def route_after_propose(state: AgentState) -> str:
    return "confirm_action" if state.get("proposed_action") else "consolidate"


def entry_route(state: AgentState) -> str:
    return "clarify" if state.get("pending_clarification") else "router"


route_after_clarify = route_after_propose
