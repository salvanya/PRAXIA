from langchain_core.messages import AIMessage, HumanMessage

from app.config import get_settings
from app.graph.state import last_user_text, new_state


def test_new_state_has_minimal_shape():
    s = new_state("hola", practice_id="p-1", thread_id="t-1")
    assert s["practice_id"] == "p-1"
    assert s["thread_id"] == "t-1"
    assert s["intent"] == ""
    assert s["retrieved"] == []
    assert s["sources"] == []
    assert s["candidate_sql"] == ""
    assert s["judge_scores"] == {}
    assert len(s["messages"]) == 1
    assert isinstance(s["messages"][0], HumanMessage)
    assert s["messages"][0].content == "hola"


def test_last_user_text_returns_latest_human_message():
    s = new_state("primera", practice_id="p-1", thread_id="t-1")
    s["messages"].append(AIMessage(content="respuesta"))
    s["messages"].append(HumanMessage(content="segunda"))
    assert last_user_text(s) == "segunda"


def test_last_user_text_empty_when_no_human():
    s = new_state("x", practice_id="p", thread_id="t")
    s["messages"] = [AIMessage(content="solo asistente")]
    assert last_user_text(s) == ""


def test_new_state_inits_proposed_action_none() -> None:
    state = new_state("hola", "pid", "tid")
    assert state["proposed_action"] is None


def test_appointment_config_defaults() -> None:
    s = get_settings()
    assert s.appt_default_duration_min == 30
    assert s.appt_name_match_limit == 5
