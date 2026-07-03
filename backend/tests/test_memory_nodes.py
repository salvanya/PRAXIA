from app.graph import memory_nodes
from app.graph.state import new_state


async def test_recall_node_populates_memories(monkeypatch) -> None:
    async def _recall(query, practice_id):
        return [{"id": "m1", "content": "Turnos de 30 min.", "kind": "hecho", "scope": "practice"}]

    async def _touch(ids):
        assert ids == ["m1"]

    monkeypatch.setattr(memory_nodes.long_term, "recall", _recall)
    monkeypatch.setattr(memory_nodes.long_term, "touch_last_used", _touch)
    out = await memory_nodes.recall_node(new_state("cuánto duran los turnos", "p", "t"))
    assert out["memories"][0]["content"] == "Turnos de 30 min."


async def test_recall_node_best_effort_on_error(monkeypatch) -> None:
    async def _boom(query, practice_id):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(memory_nodes.long_term, "recall", _boom)
    out = await memory_nodes.recall_node(new_state("x", "p", "t"))
    assert out == {"memories": []}  # no rompe el turno


async def test_recall_node_disabled(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.setattr(memory_nodes, "get_settings", lambda: Settings(memory_recall_enabled=False))
    out = await memory_nodes.recall_node(new_state("x", "p", "t"))
    assert out == {"memories": []}


async def test_reflect_node_calls_reflect_run(monkeypatch) -> None:
    from langchain_core.messages import AIMessage

    seen = {}

    async def _run(practice_id, user_text, assistant_text):
        seen["args"] = (practice_id, user_text, assistant_text)

    monkeypatch.setattr(memory_nodes.reflect, "run", _run)
    state = new_state("acordate que los turnos duran 30 min", "p", "t")
    state["messages"].append(AIMessage(content="Dale."))
    out = await memory_nodes.reflect_node(state)
    assert out == {}
    assert seen["args"] == ("p", "acordate que los turnos duran 30 min", "Dale.")


async def test_reflect_node_best_effort_on_error(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(memory_nodes.reflect, "run", _boom)
    out = await memory_nodes.reflect_node(new_state("x", "p", "t"))
    assert out == {}
