from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.action_agent import ProposalResult
from app.graph import edges, nodes
from app.graph.state import AgentState, new_state

ACTION = {
    "kind": "create_appointment",
    "summary": "Crear turno: Ana López con Dra. Gómez — 30/06 10:00–10:30",
    "params": {
        "client_id": "c1",
        "client_name": "Ana López",
        "practitioner_id": "p1",
        "practitioner_name": "Dra. Gómez",
        "start_at": "2026-06-30T10:00:00+00:00",
        "end_at": "2026-06-30T10:30:00+00:00",
        "reason": "control",
        "channel": "presencial",
        "status": "programado",
    },
}


class _Spy:
    def __init__(self, ret):  # type: ignore[no-untyped-def]
        self.ret = ret
        self.calls: list = []

    async def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((args, kwargs))
        return self.ret


def _hitl_graph():  # type: ignore[no-untyped-def]
    g = StateGraph(AgentState)
    g.add_node("propose_appointment", nodes.propose_appointment_node)
    g.add_node("confirm_appointment", nodes.confirm_appointment_node)
    g.add_edge(START, "propose_appointment")
    g.add_conditional_edges(
        "propose_appointment",
        edges.route_after_propose,
        {"confirm_appointment": "confirm_appointment", END: END},
    )
    g.add_edge("confirm_appointment", END)
    return g.compile(checkpointer=MemorySaver())


async def _fake_propose(question, practice_id, *, now, gen_llm=None):  # type: ignore[no-untyped-def]
    return ProposalResult(proposed_action=ACTION, abstained=False, message="", reason="ok")


async def test_confirm_writes_appointment_exactly_once(monkeypatch) -> None:
    spy = _Spy({"id": "appt-1", "status": "programado"})
    monkeypatch.setattr(nodes, "propose_appointment", _fake_propose)
    monkeypatch.setattr(nodes, "create_appointment", spy)
    graph = _hitl_graph()
    config = {"configurable": {"thread_id": "t-confirm"}}

    await graph.ainvoke(new_state("agendá a Ana mañana 10", "pid", "t-confirm"), config)
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_appointment",)
    assert snap.tasks[0].interrupts[0].value["kind"] == "create_appointment"
    assert spy.calls == []  # todavía no se escribió

    await graph.ainvoke(Command(resume="confirm"), config)
    assert len(spy.calls) == 1  # se escribió UNA vez (sin recomputar la propuesta)


async def test_cancel_writes_nothing(monkeypatch) -> None:
    spy = _Spy({"id": "appt-1", "status": "programado"})
    monkeypatch.setattr(nodes, "propose_appointment", _fake_propose)
    monkeypatch.setattr(nodes, "create_appointment", spy)
    graph = _hitl_graph()
    config = {"configurable": {"thread_id": "t-cancel"}}

    await graph.ainvoke(new_state("agendá a Ana mañana 10", "pid", "t-cancel"), config)
    await graph.ainvoke(Command(resume="cancel"), config)
    assert spy.calls == []
