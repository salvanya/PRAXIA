from app.models import Chunk
from app.rag import judges
from app.rag.judges import chunks_text


def _c() -> Chunk:
    return Chunk(
        text="La consulta dura 60 min.",
        page=None,
        chunk_index=0,
        document_id="1",
        title="T",
        doc_type="x",
    )


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


async def test_judge_relevance_parses_sufficient():
    llm = FakeLLM(judges.RelevanceVerdict(sufficient=True, reason="ok"))
    verdict = await judges.judge_relevance("q", [_c()], llm=llm)
    assert verdict.sufficient is True


async def test_judge_relevance_parses_insufficient():
    llm = FakeLLM(judges.RelevanceVerdict(sufficient=False, reason="off-topic"))
    verdict = await judges.judge_relevance("q", [_c()], llm=llm)
    assert verdict.sufficient is False


async def test_judge_groundedness_parses_grounded():
    llm = FakeLLM(judges.GroundednessVerdict(grounded=True, reason="ok"))
    verdict = await judges.judge_groundedness("La consulta dura 60 min.", [_c()], llm=llm)
    assert verdict.grounded is True


async def test_judge_groundedness_parses_ungrounded():
    llm = FakeLLM(judges.GroundednessVerdict(grounded=False, reason="inventado"))
    verdict = await judges.judge_groundedness("La consulta dura 90 min.", [_c()], llm=llm)
    assert verdict.grounded is False


class CapturingStructured:
    def __init__(self, value, sink):
        self._value = value
        self._sink = sink

    async def ainvoke(self, messages):
        self._sink["messages"] = messages
        return self._value


class CapturingLLM:
    def __init__(self, value, sink):
        self._value = value
        self._sink = sink

    def with_structured_output(self, schema):
        return CapturingStructured(self._value, self._sink)


async def test_relevance_with_memories_adds_memory_section_and_prompt():
    sink = {}
    llm = CapturingLLM(judges.RelevanceVerdict(sufficient=True, reason="ok"), sink)
    await judges.judge_relevance("q", [_c()], memories=[{"content": "dato de memoria"}], llm=llm)
    assert sink["messages"][0] == ("system", judges.RELEVANCE_PROMPT_WITH_MEMORY)
    human = sink["messages"][1][1]
    assert "dato de memoria" in human


async def test_relevance_without_memories_is_identical_to_today():
    sink = {}
    llm = CapturingLLM(judges.RelevanceVerdict(sufficient=True, reason="ok"), sink)
    await judges.judge_relevance("q", [_c()], llm=llm)
    assert sink["messages"][0] == ("system", judges.RELEVANCE_PROMPT)
    assert sink["messages"][1] == ("human", f"Pregunta: q\n\nFragmentos:\n{chunks_text([_c()])}")


async def test_groundedness_with_memories_uses_memory_prompt():
    sink = {}
    llm = CapturingLLM(judges.GroundednessVerdict(grounded=True, reason="ok"), sink)
    await judges.judge_groundedness("ans", [_c()], memories=[{"content": "m"}], llm=llm)
    assert sink["messages"][0] == ("system", judges.GROUNDEDNESS_PROMPT_WITH_MEMORY)
    assert "m" in sink["messages"][1][1]
