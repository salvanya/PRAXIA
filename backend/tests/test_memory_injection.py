from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import AgentState, new_state


def _one_node_graph(node):
    g = StateGraph(AgentState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


async def _run(node, state):
    graph = _one_node_graph(node)
    async for _ in graph.astream(state, stream_mode="custom"):
        pass


async def test_chitchat_injects_memories(monkeypatch) -> None:
    captured = {}

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("ok")

    monkeypatch.setattr(nodes, "_chitchat_llm", lambda: FakeLLM())
    state = new_state("¿cuánto duran los turnos?", "p", "t")
    state["memories"] = [{"content": "Los turnos duran 30 minutos.", "kind": "hecho"}]
    await _run(nodes.chitchat_node, state)
    system_texts = [m[1] for m in captured["messages"] if m[0] == "system"]
    assert any("30 minutos" in t for t in system_texts), "la memoria debe inyectarse como system"


async def test_chitchat_no_memories_no_extra_system(monkeypatch) -> None:
    captured = {}

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("ok")

    monkeypatch.setattr(nodes, "_chitchat_llm", lambda: FakeLLM())
    await _run(nodes.chitchat_node, new_state("hola", "p", "t"))  # memories=[] por new_state
    assert captured["messages"][0] == ("system", nodes.CHITCHAT_SYSTEM)
    assert sum(1 for m in captured["messages"] if m[0] == "system") == 1
