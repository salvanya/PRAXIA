from app.graph.state import AgentState

_INTENT_TO_NODE = {
    "rag": "rag",
    "sql": "sql_node",
    "action": "propose_action",
    "chitchat": "chitchat",
    "memoria": "memory_command",
    "out_of_scope": "scope_reject",
}


def route(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state["intent"], "scope_reject")


def route_after_propose(state: AgentState) -> str:
    return "confirm_action" if state.get("proposed_action") else "consolidate"


def entry_route(state: AgentState) -> str:
    return "clarify" if state.get("pending_clarification") else "router"


route_after_clarify = route_after_propose


def route_after_memory_command(state: AgentState) -> str:
    # solo el forget saltea reflect (evita re-learn); todo lo demás pasa por consolidate
    return "end" if state.get("skip_reflect") else "consolidate"
