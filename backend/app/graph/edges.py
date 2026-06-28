from langgraph.graph import END

from app.graph.state import AgentState

_INTENT_TO_NODE = {
    "rag": "rag",
    "sql": "sql_node",
    "action": "propose_appointment",
    "chitchat": "chitchat",
    "out_of_scope": "scope_reject",
}


def route(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state["intent"], "scope_reject")


def route_after_propose(state: AgentState) -> str:
    return "confirm_appointment" if state.get("proposed_action") else END
