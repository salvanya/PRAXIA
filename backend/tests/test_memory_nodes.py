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


async def test_recall_node_touch_failure_preserves_memories(monkeypatch) -> None:
    async def _recall(query, practice_id):
        return [
            {
                "id": "m2",
                "content": "Paciente prefiere turno mañana.",
                "kind": "hecho",
                "scope": "practice",
            }
        ]

    async def _touch_boom(ids):
        raise RuntimeError("pg down")

    monkeypatch.setattr(memory_nodes.long_term, "recall", _recall)
    monkeypatch.setattr(memory_nodes.long_term, "touch_last_used", _touch_boom)
    out = await memory_nodes.recall_node(new_state("turno preferido", "p", "t"))
    # touch falló pero el recall fue exitoso — las memorias NO deben perderse
    assert out["memories"][0]["content"] == "Paciente prefiere turno mañana."


async def test_recall_node_disabled(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.setattr(memory_nodes, "get_settings", lambda: Settings(memory_recall_enabled=False))
    out = await memory_nodes.recall_node(new_state("x", "p", "t"))
    assert out == {"memories": []}


async def test_consolidate_node_calls_reflect_run(monkeypatch) -> None:
    from langchain_core.messages import AIMessage

    seen = {}

    async def _run(practice_id, user_text, assistant_text):
        seen["args"] = (practice_id, user_text, assistant_text)

    monkeypatch.setattr(memory_nodes.reflect, "run", _run)
    state = new_state("acordate que los turnos duran 30 min", "p", "t")
    state["messages"].append(AIMessage(content="Dale."))
    out = await memory_nodes.consolidate_node(state)
    assert out == {}
    assert seen["args"] == ("p", "acordate que los turnos duran 30 min", "Dale.")


async def test_consolidate_node_best_effort_on_error(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(memory_nodes.reflect, "run", _boom)
    out = await memory_nodes.consolidate_node(new_state("x", "p", "t"))
    assert out == {}


async def test_consolidate_updates_summary_on_eviction(monkeypatch) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from app.config import Settings

    async def _noop_reflect(*a, **k):
        return None

    async def _fake_summary(old_summary, new_messages, *, llm=None):
        return "RESUMEN NUEVO"

    monkeypatch.setattr(memory_nodes.reflect, "run", _noop_reflect)
    monkeypatch.setattr(memory_nodes.summarize, "run", _fake_summary)
    monkeypatch.setattr(memory_nodes, "get_settings", lambda: Settings(short_term_history_window=2))

    state = new_state("t1", "p", "t")
    state["messages"] = [
        HumanMessage(content="t1"),
        AIMessage(content="a1"),
        HumanMessage(content="t2"),
        AIMessage(content="a2"),
        HumanMessage(content="t3"),
    ]
    out = await memory_nodes.consolidate_node(state)
    assert out == {"running_summary": "RESUMEN NUEVO", "summarized_count": 3}  # 5 - 2


async def test_consolidate_no_summary_when_short(monkeypatch) -> None:
    async def _noop_reflect(*a, **k):
        return None

    called = {"summary": False}

    async def _fake_summary(*a, **k):
        called["summary"] = True
        return "X"

    monkeypatch.setattr(memory_nodes.reflect, "run", _noop_reflect)
    monkeypatch.setattr(memory_nodes.summarize, "run", _fake_summary)
    out = await memory_nodes.consolidate_node(new_state("hola", "p", "t"))
    assert out == {}
    assert called["summary"] is False  # 1 msg < window → summarize ni se llama


async def test_consolidate_summary_best_effort_on_error(monkeypatch) -> None:
    from langchain_core.messages import HumanMessage

    from app.config import Settings

    async def _noop_reflect(*a, **k):
        return None

    async def _boom_summary(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(memory_nodes.reflect, "run", _noop_reflect)
    monkeypatch.setattr(memory_nodes.summarize, "run", _boom_summary)
    monkeypatch.setattr(memory_nodes, "get_settings", lambda: Settings(short_term_history_window=2))
    state = new_state("t1", "p", "t")
    state["messages"] = [HumanMessage(content=f"m{i}") for i in range(6)]
    out = await memory_nodes.consolidate_node(state)
    assert out == {}  # el summary falló pero el turno no se rompe


async def test_consolidate_caps_fold_band(monkeypatch) -> None:
    from langchain_core.messages import HumanMessage

    from app.config import Settings

    captured = {}

    async def _noop_reflect(*a, **k):
        return None

    async def _fake_summary(old_summary, new_messages, *, llm=None):
        captured["n"] = len(new_messages)
        return "RESUMEN"

    monkeypatch.setattr(memory_nodes.reflect, "run", _noop_reflect)
    monkeypatch.setattr(memory_nodes.summarize, "run", _fake_summary)
    monkeypatch.setattr(
        memory_nodes,
        "get_settings",
        lambda: Settings(short_term_history_window=2, summary_max_fold_messages=3),
    )
    state = new_state("m0", "p", "t")
    state["messages"] = [HumanMessage(content=f"m{i}") for i in range(10)]  # evict_upto = 10-2 = 8
    out = await memory_nodes.consolidate_node(state)
    # capped to 3 (already=0 → fold_to=min(8,0+3)=3); pointer advances to 3, not 8
    assert captured["n"] == 3
    assert out == {"running_summary": "RESUMEN", "summarized_count": 3}
