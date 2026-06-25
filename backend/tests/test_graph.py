from app.graph import build, edges, nodes, router
from app.graph.state import new_state


def test_route_maps_intents_to_nodes():
    assert edges.route({"intent": "rag"}) == "rag"  # type: ignore[arg-type]
    assert edges.route({"intent": "sql"}) == "sql_stub"  # type: ignore[arg-type]
    assert edges.route({"intent": "action"}) == "action_stub"  # type: ignore[arg-type]
    assert edges.route({"intent": "chitchat"}) == "chitchat"  # type: ignore[arg-type]
    assert edges.route({"intent": "out_of_scope"}) == "scope_reject"  # type: ignore[arg-type]


def test_route_defaults_to_scope_reject_on_unknown():
    assert edges.route({"intent": "garbage"}) == "scope_reject"  # type: ignore[arg-type]


async def _run_full(monkeypatch, message, intent):
    monkeypatch.setattr(router, "classify_intent", lambda *_a, **_k: _aval(intent))
    graph = build.build_graph()
    tokens = ""
    sources: list = []
    async for chunk in graph.astream(new_state(message, "p", "t"), stream_mode="custom"):
        if chunk["kind"] == "token":
            tokens += chunk["text"]
        elif chunk["kind"] == "sources":
            sources = chunk["sources"]
    return tokens, sources


async def _aval(value):
    return value


async def test_graph_routes_sql_to_stub(monkeypatch):
    tokens, sources = await _run_full(monkeypatch, "¿cuántos turnos?", "sql")
    assert tokens == nodes.STUB_MESSAGE
    assert sources == []


async def test_graph_routes_out_of_scope_to_safe_answer(monkeypatch):
    tokens, _ = await _run_full(monkeypatch, "capital de Francia", "out_of_scope")
    assert tokens == nodes.SCOPE_MESSAGE


async def test_graph_routes_rag(monkeypatch):
    async def fake_retrieve(query, practice_id=None, top_k=None):
        return []

    monkeypatch.setattr(nodes, "retrieve", fake_retrieve)
    tokens, sources = await _run_full(monkeypatch, "¿qué dice el protocolo?", "rag")
    assert tokens == nodes.ABSTAIN_MESSAGE


def test_get_default_graph_is_cached():
    assert build.get_default_graph() is build.get_default_graph()
