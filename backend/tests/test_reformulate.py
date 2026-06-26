from app.rag import reformulate


class FakeStructured:
    def __init__(self, value):
        self._value = value

    async def ainvoke(self, messages):
        return self._value


class FakeLLM:
    def __init__(self, value):
        self._value = value

    def with_structured_output(self, schema):
        return FakeStructured(self._value)


async def test_reformulate_returns_query_string():
    llm = FakeLLM(reformulate.Reformulation(query="duracion primera consulta turno"))
    out = await reformulate.reformulate("¿cuánto dura?", [], llm=llm)
    assert out == "duracion primera consulta turno"
