from app.config import Settings
from app.context import estimate_tokens, format_summary_block
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
