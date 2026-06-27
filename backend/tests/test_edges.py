from langgraph.graph import END

from app.graph.edges import _INTENT_TO_NODE, route_after_propose
from app.graph.state import new_state


def test_action_intent_routes_to_propose() -> None:
    assert _INTENT_TO_NODE["action"] == "propose_appointment"


def test_route_after_propose_to_confirm_when_action_present() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = {"kind": "create_appointment"}
    assert route_after_propose(state) == "confirm_appointment"


def test_route_after_propose_to_end_when_abstained() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = None
    assert route_after_propose(state) == END
