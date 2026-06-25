from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import AgentState, new_state
from app.models import Chunk


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
    tokens, _ = await _run(nodes.action_stub, new_state("agendá turno", "p", "t"))
    assert tokens == nodes.STUB_MESSAGE


async def test_rag_node_streams_tokens_and_sources(monkeypatch):
    async def fake_retrieve(query, practice_id=None, top_k=None):
        return [_chunk()]

    async def fake_synth(query, chunks, llm=None):
        for piece in ["Según ", "el protocolo ", "[1]."]:
            yield piece

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)
    monkeypatch.setattr(nodes, "synthesize_stream", fake_synth)

    tokens, sources = await _run(nodes.rag_node, new_state("¿cuánto dura?", "p", "t"))
    assert "[1]" in tokens
    assert sources == [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}]


async def test_rag_node_abstains_without_chunks(monkeypatch):
    async def fake_retrieve(query, practice_id=None, top_k=None):
        return []

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)

    tokens, sources = await _run(nodes.rag_node, new_state("algo raro", "p", "t"))
    assert tokens == nodes.ABSTAIN_MESSAGE
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
