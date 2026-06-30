from datetime import UTC, datetime

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agents import write_tools
from app.agents.action_agent import Clarification, ProposalResult
from app.graph import nodes
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

    async def _route(message, llm=None):  # type: ignore[no-untyped-def]
        # 1er turno → action (los siguientes van por entry→clarify, no por el router);
        # determinístico, sin Ollama.
        return "action"

    # `nodes`/`router` importan estos nombres directamente → se parchean en su módulo
    monkeypatch.setattr("app.graph.nodes.classify_write_action", _clf)
    monkeypatch.setattr("app.graph.nodes.resolve_choice", _choice)
    monkeypatch.setattr("app.graph.router.classify_intent", _route)
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
    """Fix 2: un mensaje de texto plano en un thread pausado en confirm_action NO debe
    disparar el write de la acción pendiente (garantía HITL con thread_id estable).

    Comportamiento REAL de LangGraph (MemorySaver), verificado con un diagnóstico
    determinístico: un input que NO es Command(resume=...) sobre un thread interrumpido
    DESCARTA el interrupt pendiente y RE-EJECUTA el grafo desde el entry con el mensaje
    nuevo encolado en messages. El mensaje nuevo se rutea como un turno normal (acá, con el
    router forzado a out_of_scope, va a scope_reject → END); la acción pendiente NUNCA se
    escribe (el write solo ocurre si interrupt() devuelve "confirm" vía Command). El
    proposed_action queda huérfano en el estado pero inerte (confirm_action solo se alcanza
    vía propose_action/clarify, que lo recomputan antes).

    El router se mockea (sin Ollama) para que el test sea determinístico: 1er turno
    "cancelá…" → action (llega a confirm); 2do turno "otra cosa" → out_of_scope.
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

    async def _route(message, llm=None):  # type: ignore[no-untyped-def]
        # Determinístico, sin Ollama: 1er turno "cancelá…" → action; 2do → out_of_scope.
        return "action" if "cancel" in message.lower() else "out_of_scope"

    monkeypatch.setattr("app.graph.nodes.classify_write_action", _clf)
    monkeypatch.setattr("app.graph.router.classify_intent", _route)
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

    # Paso 1: llegar a confirm_action (interrupt activo, resumable).
    await graph.ainvoke(new_state("cancelá el turno de Ana", "pid", "slotfill-plain-msg"), cfg)
    snap = await graph.aget_state(cfg)
    assert snap.next == (
        "confirm_action",
    ), f"Esperaba interrupt en confirm_action, got {snap.next}"

    # Paso 2: mandar un mensaje plano (NO Command(resume=...)) al thread interrumpido.
    await graph.ainvoke({"messages": [HumanMessage(content="otra cosa")]}, cfg)
    snap2 = await graph.aget_state(cfg)

    # El interrupt se descarta y el grafo re-ejecuta desde el entry: el mensaje nuevo se
    # rutea (out_of_scope → scope_reject → END). No se re-interrumpe ni se escribe.
    assert snap2.next == (), f"Esperaba END tras re-ejecutar el mensaje nuevo, got {snap2.next}"
    assert write_spy["n"] == 0, (
        f"write fue llamado {write_spy['n']} veces con input de texto plano — "
        "el interrupt no protegió la acción pendiente"
    )
    last = snap2.values["messages"][-1].content
    assert last == nodes.SCOPE_MESSAGE, (
        "el mensaje plano debió procesarse como turno nuevo (scope_reject), "
        f"no quedar la acción vieja; last={last!r}"
    )
