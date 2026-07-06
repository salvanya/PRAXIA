from app.config import Settings
from app.context import build_chat_messages, estimate_tokens, format_summary_block
from app.graph.state import new_state


def test_new_state_has_summary_fields():
    s = new_state("hola", "p", "t")
    assert s["running_summary"] == ""
    assert s["summarized_count"] == 0


def test_config_context_manager_defaults():
    s = Settings()
    assert s.context_token_budget == 3000
    assert s.summary_enabled is True
    assert s.summary_timeout_s == 8.0
    assert s.summary_max_words == 150


def test_estimate_tokens_heuristic():
    assert estimate_tokens("") == 1  # mínimo 1
    assert estimate_tokens("a" * 4) == 1  # ceil(4/4)
    assert estimate_tokens("a" * 5) == 2  # ceil(5/4)
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("a" * 9) == 3


def test_format_summary_block_empty_is_blank():
    assert format_summary_block("") == ""


def test_format_summary_block_frames_as_context():
    out = format_summary_block("La usuaria se llama Ana.")
    assert "La usuaria se llama Ana." in out
    assert "no son instrucciones" in out  # framing anti-inyección


def test_build_order_system_summary_memories_history():
    out = build_chat_messages(
        system="S",
        summary="RESUMEN",
        memories=[{"content": "M"}],
        history=[("human", "H")],
        budget=100000,
    )
    assert out[0] == ("system", "S")
    assert out[1][0] == "system" and "RESUMEN" in out[1][1]
    assert out[2][0] == "system" and "M" in out[2][1]
    assert out[-1] == ("human", "H")


def test_build_omits_empty_summary_and_memories():
    out = build_chat_messages(
        system="S", summary="", memories=[], history=[("human", "H")], budget=100000
    )
    assert out == [("system", "S"), ("human", "H")]


def test_build_drops_oldest_history_first_keeping_current():
    hist = [("human", "viejo1 " * 20), ("ai", "viejo2 " * 20), ("human", "actual " * 20)]
    out = build_chat_messages(system="sys", summary="", memories=[], history=hist, budget=60)
    texts = [t for _, t in out]
    assert ("system", "sys") in out
    assert hist[-1] in out  # el turno actual se preserva
    assert hist[0][1] not in texts  # el más viejo se dropeó


def test_build_drops_memories_when_no_history_droppable():
    out = build_chat_messages(
        system="sys",
        summary="",
        memories=[{"content": "memoria " * 50}],
        history=[("human", "actual")],
        budget=20,
    )
    # el bloque de memorias se removió; system + turno actual siguen
    assert ("system", "sys") in out
    assert ("human", "actual") in out
    assert not any("memoria" in t for _, t in out)


def test_build_truncates_current_turn_as_last_resort():
    out = build_chat_messages(
        system="s", summary="", memories=[], history=[("human", "x" * 10000)], budget=20
    )
    assert ("system", "s") in out
    assert out[-1][0] == "human"
    assert out[-1][1].endswith("…[truncado]")
    assert len(out[-1][1]) < 10000
