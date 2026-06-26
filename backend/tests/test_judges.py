from app.models import Chunk
from app.rag import judges


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
