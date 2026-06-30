from datetime import UTC, datetime

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents import write_tools
from app.agents.action_agent import Clarification, ProposalResult
from app.graph.build import build_graph
from app.graph.state import new_state


def _appt(aid, dt):  # type: ignore[no-untyped-def]
    return {
        "id": aid,
        "start_at": dt,
        "end_at": dt,
        "status": "programado",
        "practitioner_id": "p1",
        "practitioner_full_name": "Dra. Gómez",
    }


async def test_slotfill_client_then_appointment_then_confirm(monkeypatch) -> None:
    cands_client = [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}]
    cands_appt = [
        _appt("a1", datetime(2026, 7, 1, 14, 0, tzinfo=UTC)),
        _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC)),
    ]
    action = {
        "kind": "cancel_appointment",
        "summary": "s",
        "params": {
            "appointment_id": "a1",
            "client_name": "Ana A",
            "practitioner_name": "Dra. Gómez",
            "start_at": "2026-07-01T14:00:00+00:00",
        },
    }

    async def _clf(question, llm=None):
        return "cancel_appointment"

    async def _propose(
        question,
        practice_id,
        *,
        now,
        gen_llm=None,
        client_override=None,
        appointment_override=None,
    ):
        if client_override is None:
            return ProposalResult(
                None,
                abstained=True,
                message="m",
                reason="client_ambiguous",
                clarification=Clarification("client", cands_client, "Hay varios clientes"),
            )
        if appointment_override is None:
            return ProposalResult(
                None,
                abstained=True,
                message="m",
                reason="appointment_ambiguous",
                clarification=Clarification("appointment", cands_appt, "Tiene varios turnos"),
            )
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    write_spy = {"n": 0}

    async def _write(practice_id, params):
        write_spy["n"] += 1
        return {
            "cancelled": True,
            "id": "a1",
            "status": "cancelado",
            "start_at": datetime(2026, 7, 1, 14, 0, tzinfo=UTC),
        }

    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 1  # el usuario elige siempre la opción 1

    # `nodes` importa estos nombres directamente → se parchean en app.graph.nodes
    monkeypatch.setattr("app.graph.nodes.classify_write_action", _clf)
    monkeypatch.setattr("app.graph.nodes.resolve_choice", _choice)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "cancel_appointment",
        write_tools.WriteTool(
            kind="cancel_appointment",
            propose=_propose,
            write=_write,
            format_receipt=lambda p, r: "✅ ok",
            cancel_message="x",
        ),
    )

    graph = build_graph(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "slotfill-1"}}

    await graph.ainvoke(new_state("cancelá el turno de Ana", "pid", "slotfill-1"), cfg)
    snap = await graph.aget_state(cfg)
    assert snap.values["pending_clarification"]["stage"] == "client"

    await graph.ainvoke({"messages": [HumanMessage(content="la A")]}, cfg)
    snap = await graph.aget_state(cfg)
    assert snap.values["pending_clarification"]["stage"] == "appointment"

    await graph.ainvoke({"messages": [HumanMessage(content="el del 1")]}, cfg)
    snap = await graph.aget_state(cfg)
    assert snap.next == ("confirm_action",)  # se abrió la tarjeta

    await graph.ainvoke(Command(resume="confirm"), cfg)
    assert write_spy["n"] == 1


async def test_plain_message_while_paused_at_confirm_does_not_write(monkeypatch) -> None:
    """Fix 2: un mensaje de texto plano en un thread pausado en confirm_action
    NO debe disparar el write de la acción pendiente.

    Se construye el escenario en dos pasos:
    1. Se manda una petición que produce directamente un proposed_action (sin ambigüedad),
       de modo que el graph queda suspendido en confirm_action (snap.next == ("confirm_action",)).
    2. Se manda {"messages": [HumanMessage(...)]} plain — NOT Command(resume=...) — al mismo config.
    3. Se verifica que write_spy["n"] == 0.

    Lo que LangGraph hace con un non-Command input a un thread interrumpido:
    LangGraph (MemorySaver) lo trata como un nuevo turno de usuario y lo
    encola/merge en messages, luego reanuda la ejecución desde el inicio del grafo
    (no desde el interrupt). El nodo confirm_action se vuelve a alcanzar y llama
    interrupt() de nuevo, lo que suspende el grafo otra vez sin escribir. El write
    solo ocurre si el valor devuelto por interrupt() == "confirm" (vía Command).
    En consecuencia: el write_spy permanece en 0 y el grafo queda pausado de nuevo.
    """
    action = {
        "kind": "cancel_appointment",
        "summary": "s",
        "params": {
            "appointment_id": "a1",
            "client_name": "Ana A",
            "practitioner_name": "Dra. Gómez",
            "start_at": "2026-07-01T14:00:00+00:00",
        },
    }

    async def _clf(question, llm=None):
        return "cancel_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None, **kw):
        # Devuelve acción directamente (sin ambigüedad) → graph llega a confirm_action.
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    write_spy = {"n": 0}

    async def _write(practice_id, params):
        write_spy["n"] += 1
        return {
            "cancelled": True,
            "id": "a1",
            "status": "cancelado",
            "start_at": datetime(2026, 7, 1, 14, 0, tzinfo=UTC),
        }

    monkeypatch.setattr("app.graph.nodes.classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "cancel_appointment",
        write_tools.WriteTool(
            kind="cancel_appointment",
            propose=_propose,
            write=_write,
            format_receipt=lambda p, r: "ok",
            cancel_message="cancelado",
        ),
    )

    graph = build_graph(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "slotfill-plain-msg"}}

    # Paso 1: llegar a confirm_action (interrupt activo).
    await graph.ainvoke(new_state("cancelá el turno de Ana", "pid", "slotfill-plain-msg"), cfg)
    snap = await graph.aget_state(cfg)
    assert snap.next == (
        "confirm_action",
    ), f"Expected interrupt at confirm_action, got {snap.next}"

    # Paso 2: enviar un mensaje plano (NO Command(resume=...)) al thread interrumpido.
    # Observamos qué hace LangGraph: re-ejecuta desde el inicio con el mensaje extra
    # y vuelve a interrumpirse en confirm_action sin llamar a write.
    await graph.ainvoke({"messages": [HumanMessage(content="otra cosa")]}, cfg)

    # Paso 3: write NO fue llamado (el interrupt protege la escritura).
    assert write_spy["n"] == 0, (
        f"write fue llamado {write_spy['n']} veces con input de texto plano — "
        "el interrupt no protegió la acción pendiente"
    )
