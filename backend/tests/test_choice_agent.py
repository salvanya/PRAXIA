from app.agents import choice_agent
from app.agents.choice_agent import Choice


class _FakeStructured:
    def __init__(self, value):  # type: ignore[no-untyped-def]
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class FakeLLM:
    def __init__(self, value):  # type: ignore[no-untyped-def]
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


async def test_returns_valid_choice() -> None:
    r = await choice_agent.resolve_choice(
        "1. A\n2. B", "la segunda", n=2, gen_llm=FakeLLM(Choice(choice=2))
    )
    assert r == 2


async def test_zero_when_unclear() -> None:
    r = await choice_agent.resolve_choice(
        "1. A\n2. B", "no sé", n=2, gen_llm=FakeLLM(Choice(choice=0))
    )
    assert r == 0


async def test_out_of_range_is_zero() -> None:
    r = await choice_agent.resolve_choice("1. A", "el 5", n=1, gen_llm=FakeLLM(Choice(choice=5)))
    assert r == 0


async def test_exception_is_zero() -> None:
    r = await choice_agent.resolve_choice("1. A", "x", n=1, gen_llm=FakeLLM(RuntimeError("boom")))
    assert r == 0
