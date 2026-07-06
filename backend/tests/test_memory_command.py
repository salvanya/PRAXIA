from langgraph.graph import END, START, StateGraph

from app.graph import memory_command
from app.graph.memory_command import MemoryCommand
from app.graph.state import AgentState, new_state


def _one_node_graph(node):
    g = StateGraph(AgentState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile()


async def _run(node, state):
    graph = _one_node_graph(node)
    tokens = ""
    async for chunk in graph.astream(state, stream_mode="custom"):
        if chunk["kind"] == "token":
            tokens += chunk["text"]
    return tokens


def _match(mid="m1", content="Los turnos duran 30 minutos.", score=0.95, kind="hecho"):
    return {"id": mid, "content": content, "kind": kind, "scope": "practice", "score": score}


async def test_forget_confident_deletes_and_echoes(monkeypatch):
    calls = {}

    async def _extract(text):
        return MemoryCommand(operation="forget", target="duración de turnos", new_value="")

    async def _recall(query, practice_id):
        return [_match()]

    async def _forget(practice_id, ids):
        calls["forget"] = ids
        return len(ids)

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    tokens = await _run(
        memory_command.memory_command_node, new_state("olvidá lo de la duración", "p", "t")
    )
    assert calls["forget"] == ["m1"]
    assert "olvidé" in tokens.lower()


async def test_no_match_does_not_delete(monkeypatch):
    calls = {"forget": False}

    async def _extract(text):
        return MemoryCommand(operation="forget", target="algo inexistente", new_value="")

    async def _recall(query, practice_id):
        return []

    async def _forget(practice_id, ids):
        calls["forget"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    tokens = await _run(memory_command.memory_command_node, new_state("olvidá X", "p", "t"))
    assert "no tengo nada" in tokens.lower() and calls["forget"] is False


async def test_ambiguous_asks_and_does_not_delete(monkeypatch):
    calls = {"forget": False}

    async def _extract(text):
        return MemoryCommand(operation="forget", target="la duración", new_value="")

    async def _recall(query, practice_id):
        return [_match("m1", "Turnos de 30 min.", 0.72), _match("m2", "Turnos de 45 min.", 0.70)]

    async def _forget(practice_id, ids):
        calls["forget"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    tokens = await _run(
        memory_command.memory_command_node, new_state("olvidá la duración", "p", "t")
    )
    assert "parecidas" in tokens.lower() and calls["forget"] is False


async def test_none_falls_back_to_chitchat(monkeypatch):
    called = {"chitchat": False, "forget": False}

    async def _extract(text):
        return MemoryCommand(operation="none", target="", new_value="")

    async def _chitchat(state):
        called["chitchat"] = True
        return {"sources": [], "messages": []}

    async def _forget(practice_id, ids):
        called["forget"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command, "chitchat_node", _chitchat)
    monkeypatch.setattr(memory_command.long_term, "forget", _forget)
    await _run(memory_command.memory_command_node, new_state("hola", "p", "t"))
    assert called["chitchat"] is True and called["forget"] is False


async def test_extract_none_falls_back_to_chitchat(monkeypatch):
    called = {"chitchat": False}

    async def _extract(text):
        return None

    async def _chitchat(state):
        called["chitchat"] = True
        return {"sources": [], "messages": []}

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command, "chitchat_node", _chitchat)
    await _run(memory_command.memory_command_node, new_state("cualquier cosa", "p", "t"))
    assert called["chitchat"] is True


async def test_correct_supersedes_with_new_value(monkeypatch):
    calls = {}

    async def _extract(text):
        return MemoryCommand(
            operation="correct",
            target="duración de turnos",
            new_value="Los turnos duran 60 minutos.",
        )

    async def _recall(query, practice_id):
        return [_match()]

    async def _store(
        practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()
    ):
        calls["store"] = {"content": content, "supersede_ids": list(supersede_ids)}
        return "new1"

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "store", _store)
    tokens = await _run(
        memory_command.memory_command_node, new_state("corregí la duración", "p", "t")
    )
    assert calls["store"] == {"content": "Los turnos duran 60 minutos.", "supersede_ids": ["m1"]}
    assert "corregido" in tokens.lower()


async def test_correct_without_value_asks_and_does_not_store(monkeypatch):
    calls = {"store": False}

    async def _extract(text):
        return MemoryCommand(operation="correct", target="la duración", new_value="")

    async def _recall(query, practice_id):
        return [_match()]

    async def _store(*a, **k):
        calls["store"] = True

    monkeypatch.setattr(memory_command, "extract_command", _extract)
    monkeypatch.setattr(memory_command.long_term, "recall", _recall)
    monkeypatch.setattr(memory_command.long_term, "store", _store)
    tokens = await _run(
        memory_command.memory_command_node, new_state("corregí la duración", "p", "t")
    )
    assert "dato correcto" in tokens.lower() and calls["store"] is False
