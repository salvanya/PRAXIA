from langchain_core.messages import HumanMessage

from app.main import select_chat_input


def test_first_turn_returns_full_initial_state() -> None:
    inp = select_chat_input({}, "hola", "pid", "tid")
    assert inp["thread_id"] == "tid" and inp["practice_id"] == "pid"
    assert len(inp["messages"]) == 1 and inp["messages"][0].content == "hola"
    assert inp["proposed_action"] is None


def test_subsequent_turn_returns_incremental_patch_only() -> None:
    inp = select_chat_input({"messages": ["prev"], "practice_id": "pid"}, "segundo", "pid", "tid")
    assert set(inp.keys()) == {"messages"}  # NO incluye los demás campos → no los pisa
    assert isinstance(inp["messages"][0], HumanMessage)
    assert inp["messages"][0].content == "segundo"
