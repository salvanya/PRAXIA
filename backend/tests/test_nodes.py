from langchain_core.messages import AIMessage, HumanMessage
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


async def _final(node, state):
    """Corre un nodo y devuelve el AgentState final (parche aplicado)."""
    graph = _one_node_graph(node)
    return await graph.ainvoke(state)


def _appt_cand(aid="a1", dt=None):
    from datetime import UTC, datetime

    dt = dt or datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    return {
        "id": aid,
        "start_at": dt,
        "end_at": dt,
        "status": "programado",
        "practitioner_id": "p1",
        "practitioner_full_name": "Dra. Gómez",
    }


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


async def test_chitchat_includes_recent_history(monkeypatch):
    captured = {}

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("ok")

    monkeypatch.setattr(nodes, "_chitchat_llm", lambda: FakeLLM())
    state = new_state("¿cómo me llamo?", "p", "t")
    state["messages"] = [
        HumanMessage(content="soy Ana"),
        AIMessage(content="¡Hola Ana!"),
        HumanMessage(content="¿cómo me llamo?"),
    ]
    await _run(nodes.chitchat_node, state)
    assert captured["messages"][0] == ("system", nodes.CHITCHAT_SYSTEM)
    assert ("human", "soy Ana") in captured["messages"]
    assert ("ai", "¡Hola Ana!") in captured["messages"]


async def test_chitchat_window_zero_sends_no_history(monkeypatch):
    """Fix 3: short_term_history_window=0 debe enviar SOLO el system message (sin historial).
    Antes del fix, [-0:] == lista completa → se incluía todo el historial (bug silencioso)."""
    captured = {}

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("ok")

    monkeypatch.setattr(nodes, "_chitchat_llm", lambda: FakeLLM())

    # Forzar window=0 sin cambiar el env: parcheamos get_settings devolviendo un Settings
    # con short_term_history_window=0.
    from app.config import Settings

    monkeypatch.setattr(nodes, "get_settings", lambda: Settings(short_term_history_window=0))

    state = new_state("hola", "p", "t")
    state["messages"] = [
        HumanMessage(content="soy Ana"),
        AIMessage(content="¡Hola Ana!"),
        HumanMessage(content="hola"),
    ]
    await _run(nodes.chitchat_node, state)
    # Solo debe llegar el system prompt, sin ningún turno de historial.
    assert captured["messages"] == [
        ("system", nodes.CHITCHAT_SYSTEM)
    ], f"window=0 no debe incluir historial, pero se recibió: {captured['messages']}"


async def test_propose_action_clarification_sets_pending(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import Clarification, ProposalResult

    async def _clf(question, llm=None):
        return "cancel_appointment"

    async def _propose(question, practice_id, *, now, gen_llm=None, **kw):
        return ProposalResult(
            None,
            abstained=True,
            message="m",
            reason="appointment_ambiguous",
            clarification=Clarification(
                "appointment", [_appt_cand("a1"), _appt_cand("a2")], "Tiene varios turnos"
            ),
        )

    monkeypatch.setattr(nodes, "classify_write_action", _clf)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "cancel_appointment",
        write_tools.WriteTool(
            kind="cancel_appointment",
            propose=_propose,
            write=write_tools._write_cancel,
            format_receipt=write_tools.format_cancel_receipt,
            cancel_message="x",
        ),
    )
    out = await _final(nodes.propose_action_node, new_state("cancelá", "p", "t"))
    pending = out["pending_clarification"]
    assert pending["stage"] == "appointment" and len(pending["candidates"]) == 2
    assert pending["kind"] == "cancel_appointment" and pending["overrides"] == {}


async def test_clarify_maps_and_proposes(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import ProposalResult

    action = {"kind": "cancel_appointment", "summary": "s", "params": {"appointment_id": "a1"}}

    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 1

    async def _propose(
        question,
        practice_id,
        *,
        now,
        gen_llm=None,
        client_override=None,
        appointment_override=None,
    ):
        assert appointment_override is not None  # el slot elegido se pasó como override
        return ProposalResult(proposed_action=action, abstained=False, message="", reason="ok")

    monkeypatch.setattr(nodes, "resolve_choice", _choice)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "cancel_appointment",
        write_tools.WriteTool(
            kind="cancel_appointment",
            propose=_propose,
            write=write_tools._write_cancel,
            format_receipt=write_tools.format_cancel_receipt,
            cancel_message="x",
        ),
    )
    state = new_state("el primero", "p", "t")
    state["pending_clarification"] = {
        "kind": "cancel_appointment",
        "stage": "appointment",
        "candidates": [_appt_cand("a1")],
        "question": "cancelá el turno de Ana",
        "overrides": {},
    }
    out = await _final(nodes.clarify_node, state)
    assert out["proposed_action"] == action and out["pending_clarification"] is None


async def test_clarify_no_match_clears_and_retries(monkeypatch):
    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 0  # no mapea

    called = {"propose": False}

    async def _propose(*a, **k):
        called["propose"] = True

    from app.agents import write_tools

    monkeypatch.setattr(nodes, "resolve_choice", _choice)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "cancel_appointment",
        write_tools.WriteTool(
            kind="cancel_appointment",
            propose=_propose,
            write=write_tools._write_cancel,
            format_receipt=write_tools.format_cancel_receipt,
            cancel_message="x",
        ),
    )
    state = new_state("cualquier cosa", "p", "t")
    state["pending_clarification"] = {
        "kind": "cancel_appointment",
        "stage": "client",
        "candidates": [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}],
        "question": "cancelá el turno de Ana",
        "overrides": {},
    }
    out = await _final(nodes.clarify_node, state)
    assert out["pending_clarification"] is None and not called["propose"]
    assert "No identifiqué" in out["messages"][-1].content


async def test_clarify_chains_client_then_appointment(monkeypatch):
    from app.agents import write_tools
    from app.agents.action_agent import Clarification, ProposalResult

    async def _choice(numbered, reply, *, n, gen_llm=None):
        return 1

    async def _propose(
        question,
        practice_id,
        *,
        now,
        gen_llm=None,
        client_override=None,
        appointment_override=None,
    ):
        assert client_override is not None  # el cliente elegido se fijó
        return ProposalResult(
            None,
            abstained=True,
            message="m",
            reason="appointment_ambiguous",
            clarification=Clarification(
                "appointment", [_appt_cand("a1"), _appt_cand("a2")], "Tiene varios"
            ),
        )

    monkeypatch.setattr(nodes, "resolve_choice", _choice)
    monkeypatch.setitem(
        write_tools.REGISTRY,
        "cancel_appointment",
        write_tools.WriteTool(
            kind="cancel_appointment",
            propose=_propose,
            write=write_tools._write_cancel,
            format_receipt=write_tools.format_cancel_receipt,
            cancel_message="x",
        ),
    )
    state = new_state("la González", "p", "t")
    state["pending_clarification"] = {
        "kind": "cancel_appointment",
        "stage": "client",
        "candidates": [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}],
        "question": "cancelá el turno de Ana",
        "overrides": {},
    }
    out = await _final(nodes.clarify_node, state)
    pending = out["pending_clarification"]
    assert pending["stage"] == "appointment"
    assert pending["overrides"]["client"] == {"id": "1", "full_name": "Ana A"}


async def _run_tables(node, state):
    """Corre un nodo y devuelve la lista de chunks 'table' emitidos."""
    graph = _one_node_graph(node)
    tables = []
    async for chunk in graph.astream(state, stream_mode="custom"):
        if chunk["kind"] == "table":
            tables.append(chunk)
    return tables


async def test_sql_node_emits_table_for_tabular_result(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(
            sql="SELECT full_name FROM clients",
            rows=[{"full_name": "Ana"}, {"full_name": "Beto"}],
            columns=["full_name"],
        )

    async def _fake_synth(question, rows, columns, llm=None):
        return "Encontré 2 resultado(s)."

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    monkeypatch.setattr(nodes, "synthesize_sql_answer", _fake_synth)
    tables = await _run_tables(nodes.sql_node, new_state("listá clientes", "p", "t"))
    assert len(tables) == 1
    assert tables[0]["columns"] == ["full_name"]
    assert tables[0]["rows"] == [{"full_name": "Ana"}, {"full_name": "Beto"}]
    assert tables[0]["sql"] == "SELECT full_name FROM clients"


async def test_sql_node_no_table_for_scalar(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql="SELECT count(*)", rows=[{"total": 12}], columns=["total"])

    async def _fake_synth(question, rows, columns, llm=None):
        return "Tenés 12 turnos."

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    monkeypatch.setattr(nodes, "synthesize_sql_answer", _fake_synth)
    tables = await _run_tables(nodes.sql_node, new_state("¿cuántos turnos?", "p", "t"))
    assert tables == []


async def test_sql_node_no_table_when_abstained(monkeypatch):
    from app.agents.sql_agent import SqlResult

    async def _fake_answer(question, practice_id, **kw):
        return SqlResult(sql=None, abstained=True, reason="x")

    monkeypatch.setattr(nodes, "answer_structured", _fake_answer)
    tables = await _run_tables(nodes.sql_node, new_state("algo raro", "p", "t"))
    assert tables == []
