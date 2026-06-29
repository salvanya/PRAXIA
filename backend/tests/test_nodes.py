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


async def test_sql_node_emits_synthesized_answer(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql="SELECT 1", rows=[{"total": 12}], columns=["total"])

    async def _fake_synth(question, rows, columns, llm=None):
        return "Tenés 12 turnos esta semana."

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    monkeypatch.setattr(nodes, "synthesize_sql_answer", _fake_synth)
    tokens, sources = await _run(nodes.sql_node, new_state("¿cuántos turnos?", "p", "t"))
    assert tokens == "Tenés 12 turnos esta semana."
    assert sources == []


async def test_sql_node_abstains_with_no_sources(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql=None, abstained=True, reason="x")

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    tokens, sources = await _run(nodes.sql_node, new_state("algo raro", "p", "t"))
    assert tokens == nodes.SQL_ABSTAIN_MESSAGE
    assert sources == []


async def test_propose_action_unsupported_emits_capabilities(monkeypatch):
    async def _clf(question, llm=None):
        return "unsupported"

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    tokens, sources = await _run(nodes.propose_action_node, new_state("cancelá el turno", "p", "t"))
    assert "agendar turnos" in tokens and "registrar interacciones" in tokens
    assert "cancelar turnos" in tokens
    assert "reprogramar" in tokens and "actualizar datos de clientes" in tokens
    assert sources == []


async def test_propose_action_abstains_from_tool(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import ProposalResult

    async def _clf(question, llm=None):
        return "create_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None):
        return ProposalResult(
            proposed_action=None,
            abstained=True,
            message="No encontré al cliente.",
            reason="client_not_found",
        )

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "create_appointment",
        write_tools.WriteTool(
            kind="create_appointment",
            propose=_propose,
            write=write_tools._write_appointment,
            format_receipt=write_tools.format_appointment_receipt,
            cancel_message="x",
        ),
    )
    tokens, sources = await _run(nodes.propose_action_node, new_state("agendá", "p", "t"))
    assert tokens == "No encontré al cliente."
    assert sources == []


async def test_propose_action_happy_returns_action_without_emitting(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import ProposalResult

    action = {"kind": "create_appointment", "summary": "s", "params": {}}

    async def _clf(question, llm=None):
        return "create_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None):
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "create_appointment",
        write_tools.WriteTool(
            kind="create_appointment",
            propose=_propose,
            write=write_tools._write_appointment,
            format_receipt=write_tools.format_appointment_receipt,
            cancel_message="x",
        ),
    )
    tokens, sources = await _run(nodes.propose_action_node, new_state("agendá", "p", "t"))
    assert tokens == ""  # camino feliz: no emite (la tarjeta sale del interrupt)
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


async def test_rag_node_replays_long_answer_without_loss(monkeypatch):
    answer = "abcdefghij " * 10  # 110 chars, crosses several 24-char slice boundaries
    result = {
        "abstained": False,
        "answer": answer,
        "sources": [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}],
        "reranked": [_chunk()],
    }
    monkeypatch.setattr(nodes, "crag_app", FakeCragApp(result))
    tokens, sources = await _run(nodes.rag_node, new_state("¿algo largo?", "p", "t"))
    assert tokens == answer
    assert sources == result["sources"]


async def test_propose_action_classifier_exception_is_fail_closed(monkeypatch):
    async def _boom(question, llm=None):
        raise RuntimeError("classifier down")

    monkeypatch.setattr(nodes, "classify_write_action", _boom)
    tokens, sources = await _run(nodes.propose_action_node, new_state("agendá", "p", "t"))
    assert "agendar turnos" in tokens  # cae a 'unsupported' → mensaje de capacidades, sin crash
    assert sources == []
