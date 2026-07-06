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
    assert router.INTENTS == ("rag", "sql", "action", "chitchat", "memoria", "out_of_scope")


async def test_classify_intent_accepts_memoria():
    assert await router.classify_intent("olvidá eso", llm=FakeRouterLLM("memoria")) == "memoria"


@pytest.mark.llm
@pytest.mark.integration
async def test_real_e4b_classifies_greeting_as_chitchat():
    intent = await router.classify_intent("hola, ¿cómo va?")
    assert intent == "chitchat"


class FakeSequenceLLM:
    """Devuelve un content por llamada a ainvoke (para ejercitar el retry)."""

    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.calls = 0

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        self.calls += 1
        return AIMessage(content=self._contents[min(len(self._contents) - 1, self.calls - 1)])


async def test_classify_intent_substring_fallback():
    # el modelo envuelve la intención en una frase → match por substring
    llm = FakeSequenceLLM("la intención es action")
    assert await router.classify_intent("agendá algo", llm=llm) == "action"


async def test_classify_intent_retries_then_parses():
    # respuesta no clara en el 1er intento, válida en el 2do → reintenta y parsea
    llm = FakeSequenceLLM("mmm no sé", "sql")
    assert await router.classify_intent("¿cuántos turnos?", llm=llm) == "sql"
    assert llm.calls == 2


async def test_classify_intent_falls_back_to_chitchat_when_undecided():
    # el modelo nunca devuelve una intención reconocible → fallback seguro a chitchat
    llm = FakeSequenceLLM("???")
    assert await router.classify_intent("xyz", llm=llm) == "chitchat"
