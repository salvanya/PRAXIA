from app.graph.state import AgentState

_INTENT_TO_NODE = {
    "rag": "rag",
    "sql": "sql_stub",
    "action": "action_stub",
    "chitchat": "chitchat",
    "out_of_scope": "scope_reject",
}


def route(state: AgentState) -> str:
    return _INTENT_TO_NODE.get(state["intent"], "scope_reject")
