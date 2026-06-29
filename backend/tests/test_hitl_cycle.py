import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents import write_tools
from app.agents.action_agent import ProposalResult
from app.agents.write_tools import WriteTool
from app.graph import edges, nodes
from app.graph.state import AgentState, new_state

APPOINTMENT = {
    "kind": "create_appointment",
    "summary": "Crear turno: Ana López con Dra. Gómez — 30/06 10:00–10:30",
    "params": {"client_id": "c1"},
}
INTERACTION = {
    "kind": "log_interaction",
    "summary": "Registrar llamada de Ana López — «confirmó el turno»",
    "params": {"client_id": "c1"},
}
CANCELLATION = {
    "kind": "cancel_appointment",
    "summary": "Cancelar el turno de Ana López con Dra. Gómez el 01/07 10:00 (UTC)",
    "params": {"appointment_id": "a1"},
}
RESCHEDULE = {
    "kind": "reschedule_appointment",
    "summary": "Reprogramar el turno de Ana López con Dra. Gómez: 01/07 10:00 → 03/07 15:00 (UTC)",
    "params": {"appointment_id": "a1"},
}
UPDATE_CLIENT = {
    "kind": "update_client",
    "summary": "Actualizar Ana López: teléfono 11-1111-1111 → 11-2233-4455",
    "params": {"client_id": "c1"},
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
    g.add_node("propose_action", nodes.propose_action_node)
    g.add_node("confirm_action", nodes.confirm_action_node)
    g.add_edge(START, "propose_action")
    g.add_conditional_edges(
        "propose_action",
        edges.route_after_propose,
        {"confirm_action": "confirm_action", END: END},
    )
    g.add_edge("confirm_action", END)
    return g.compile(checkpointer=MemorySaver())


def _install(monkeypatch, kind, action, write_spy):  # type: ignore[no-untyped-def]
    async def _clf(question, llm=None):  # type: ignore[no-untyped-def]
        return kind

    async def _propose(question, practice_id, *, now, gen_llm=None):  # type: ignore[no-untyped-def]
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        kind,
        WriteTool(
            kind=kind,
            propose=_propose,
            write=write_spy,
            format_receipt=lambda params, row: "✅ ok",
            cancel_message="cancelado",
        ),
    )


@pytest.mark.parametrize(
    "kind,action",
    [
        ("create_appointment", APPOINTMENT),
        ("log_interaction", INTERACTION),
        ("cancel_appointment", CANCELLATION),
        ("reschedule_appointment", RESCHEDULE),
        ("update_client", UPDATE_CLIENT),
    ],
)
async def test_confirm_writes_exactly_once(monkeypatch, kind, action) -> None:
    spy = _Spy({"id": "row-1", "status": "programado", "occurred_at": None, "type": "llamada"})
    _install(monkeypatch, kind, action, spy)
    graph = _hitl_graph()
    tid = f"t-confirm-{kind}"
    config = {"configurable": {"thread_id": tid}}

    await graph.ainvoke(new_state("hacé algo", "pid", tid), config)
    snap = await graph.aget_state(config)
    assert snap.next == ("confirm_action",)
    assert snap.tasks[0].interrupts[0].value["kind"] == kind
    assert spy.calls == []  # nada escrito todavía

    await graph.ainvoke(Command(resume="confirm"), config)
    assert len(spy.calls) == 1  # se escribió UNA vez (sin recomputar la propuesta)


@pytest.mark.parametrize(
    "kind,action",
    [
        ("create_appointment", APPOINTMENT),
        ("log_interaction", INTERACTION),
        ("cancel_appointment", CANCELLATION),
        ("reschedule_appointment", RESCHEDULE),
        ("update_client", UPDATE_CLIENT),
    ],
)
async def test_cancel_writes_nothing(monkeypatch, kind, action) -> None:
    spy = _Spy({"id": "row-1"})
    _install(monkeypatch, kind, action, spy)
    graph = _hitl_graph()
    tid = f"t-cancel-{kind}"
    config = {"configurable": {"thread_id": tid}}

    await graph.ainvoke(new_state("hacé algo", "pid", tid), config)
    await graph.ainvoke(Command(resume="cancel"), config)
    assert spy.calls == []
