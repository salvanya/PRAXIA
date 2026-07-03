from app.graph.edges import _INTENT_TO_NODE, entry_route, route_after_propose
from app.graph.state import new_state


def test_action_intent_routes_to_propose() -> None:
    assert _INTENT_TO_NODE["action"] == "propose_action"


def test_route_after_propose_to_confirm_when_action_present() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = {"kind": "create_appointment"}
    assert route_after_propose(state) == "confirm_action"


def test_route_after_propose_to_reflect_when_abstained() -> None:
    state = new_state("x", "p", "t")
    state["proposed_action"] = None
    assert route_after_propose(state) == "reflect"


def test_entry_route_to_clarify_when_pending() -> None:
    state = new_state("x", "p", "t")
    state["pending_clarification"] = {"kind": "cancel_appointment", "stage": "client"}
    assert entry_route(state) == "clarify"


def test_entry_route_to_router_when_no_pending() -> None:
    assert entry_route(new_state("x", "p", "t")) == "router"
