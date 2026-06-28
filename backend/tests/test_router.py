import pytest
from langchain_core.messages import AIMessage

from app.graph import router
from app.graph.state import new_state


class FakeRouterLLM:
    """Plain-text fake matching the new classify_intent interface (no with_structured_output)."""

    def __init__(self, intent: str):
        self._intent = intent

    async def ainvoke(self, _messages):
        return AIMessage(content=self._intent)


async def test_classify_intent_returns_enum_value():
    intent = await router.classify_intent("hola", llm=FakeRouterLLM("chitchat"))
    assert intent == "chitchat"


async def test_router_node_sets_intent_from_last_human():
    state = new_state("¿cuántos turnos esta semana?", practice_id="p", thread_id="t")
    # inyectamos un llm fake vía monkeypatch del factory interno
    patch = router._router_llm
    router._router_llm = lambda: FakeRouterLLM("sql")  # type: ignore[assignment]
    try:
        out = await router.router_node(state)
    finally:
        router._router_llm = patch  # type: ignore[assignment]
    assert out == {"intent": "sql"}


def test_intents_tuple_is_the_contract():
    assert router.INTENTS == ("rag", "sql", "action", "chitchat", "out_of_scope")


@pytest.mark.llm
@pytest.mark.integration
async def test_real_e4b_classifies_greeting_as_chitchat():
    intent = await router.classify_intent("hola, ¿cómo va?")
    assert intent == "chitchat"
