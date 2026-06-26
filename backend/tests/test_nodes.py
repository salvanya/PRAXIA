from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import AgentState, new_state
from app.models import Chunk
from app.rag.synthesize import ABSTAIN_MESSAGE


def _one_node_graph(node):
    g = StateGraph(AgentState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


async def _run(node, state):
    """Devuelve (tokens_concatenados, sources, parche_final)."""
    graph = _one_node_graph(node)
    tokens = ""
    sources: list = []
    async for chunk in graph.astream(state, stream_mode="custom"):
        if chunk["kind"] == "token":
            tokens += chunk["text"]
        elif chunk["kind"] == "sources":
            sources = chunk["sources"]
    return tokens, sources


def _chunk() -> Chunk:
    return Chunk(
        text="La primera consulta dura 60 minutos.",
        page=2,
        chunk_index=0,
        document_id="doc-1",
        title="Protocolo",
        doc_type="protocolo",
    )


async def test_scope_reject_streams_fixed_message_no_sources():
    tokens, sources = await _run(nodes.scope_reject_node, new_state("capital de Francia", "p", "t"))
    assert tokens == nodes.SCOPE_MESSAGE
    assert sources == []


async def test_sql_stub_streams_not_available():
    tokens, sources = await _run(nodes.sql_stub, new_state("cuántos turnos", "p", "t"))
    assert tokens == nodes.STUB_MESSAGE
    assert sources == []


async def test_action_stub_streams_not_available():
    tokens, sources = await _run(nodes.action_stub, new_state("agendá turno", "p", "t"))
    assert tokens == nodes.STUB_MESSAGE
    assert sources == []


class FakeCragApp:
    def __init__(self, result: dict):
        self._result = result

    async def ainvoke(self, state):
        return self._result


async def test_rag_node_emits_answer_and_sources(monkeypatch):
    result = {
        "abstained": False,
        "answer": "Según el protocolo [1].",
        "sources": [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}],
        "reranked": [_chunk()],
    }
    monkeypatch.setattr(nodes, "crag_app", FakeCragApp(result))
    tokens, sources = await _run(nodes.rag_node, new_state("¿cuánto dura?", "p", "t"))
    assert "[1]" in tokens
    assert sources == [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}]


async def test_rag_node_abstains_emits_no_sources(monkeypatch):
    result = {"abstained": True, "answer": ABSTAIN_MESSAGE, "sources": [], "reranked": []}
    monkeypatch.setattr(nodes, "crag_app", FakeCragApp(result))
    tokens, sources = await _run(nodes.rag_node, new_state("algo raro", "p", "t"))
    assert tokens == ABSTAIN_MESSAGE
    assert sources == []


async def test_chitchat_streams_with_fake_llm(monkeypatch):
    class FakeMsg:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            for token in ["¡Hola! ", "¿En qué ", "te ayudo?"]:
                yield FakeMsg(token)

    monkeypatch.setattr(nodes, "_chitchat_llm", lambda: FakeLLM())

    tokens, sources = await _run(nodes.chitchat_node, new_state("hola", "p", "t"))
    assert "Hola" in tokens
    assert sources == []
