import uuid

import pytest
from langchain_core.messages import HumanMessage

from app.graph.build import build_graph
from app.graph.state import new_state

pytestmark = pytest.mark.llm

PRACTICE = "00000000-0000-0000-0000-000000000001"


async def test_running_summary_carries_evicted_fact(monkeypatch) -> None:
    """Un hecho dicho en el turno 1 (que cae fuera de la ventana verbatim=2) sigue
    disponible vía el running_summary en un turno posterior."""
    # Ventana chica → desalojo rápido; reflexión LP apagada para aislar el summary.
    monkeypatch.setenv("SHORT_TERM_HISTORY_WINDOW", "2")
    monkeypatch.setenv("MEMORY_REFLECT_ENABLED", "false")

    graph = build_graph(checkpointer=None)
    thread = uuid.uuid4().hex

    # Turno 1: planta el hecho.
    state = new_state("Me llamo Ana y soy nutricionista.", PRACTICE, thread)
    state = await graph.ainvoke(state)

    # Turnos 2 y 3: relleno (empujan el turno 1 fuera de la ventana verbatim).
    for filler in ("¿Qué días conviene agendar?", "Gracias, muy claro."):
        state["messages"].append(HumanMessage(content=filler))
        state = await graph.ainvoke(state)

    # El running_summary debió capturar el hecho desalojado.
    assert state["running_summary"], "el summary debió poblarse tras el desalojo"
    assert (
        "ana" in state["running_summary"].lower()
    ), f"el summary debió retener el nombre; got: {state['running_summary']!r}"

    # Turno 4: prueba que el grafo ensambla summary+contexto en un turno chitchat real
    # sin error.  Que el 12b VERBALICE el hecho recordado depende del seguimiento del
    # prompt de chitchat ("No inventes datos de la práctica") y es variance-prone; la
    # verificación autoritativa de continuidad es la aserción primaria sobre
    # running_summary más arriba.  La verbalización se mejora vía DSPy (fast-follow).
    state["messages"].append(HumanMessage(content="¿Cómo me llamo?"))
    state = await graph.ainvoke(state)
    last = state["messages"][-1].content
    assert last, "el grafo debió producir una respuesta"
