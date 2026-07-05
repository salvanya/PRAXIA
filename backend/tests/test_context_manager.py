from app.config import Settings
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
