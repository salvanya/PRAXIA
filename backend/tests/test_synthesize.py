from app.models import Chunk
from app.rag import synthesize


class FakeChunkMsg:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    async def astream(self, messages):
        # Echo a deterministic answer that cites source [1].
        for token in ["Según ", "el ", "protocolo ", "[1]."]:
            yield FakeChunkMsg(token)


def _chunk() -> Chunk:
    return Chunk(
        text="La primera consulta dura 60 minutos.",
        page=2,
        chunk_index=0,
        document_id="doc-1",
        title="Protocolo",
        doc_type="protocolo",
    )


async def test_abstains_without_context():
    out = "".join([t async for t in synthesize.synthesize_stream("hola", [])])
    assert out == synthesize.ABSTAIN_MESSAGE


async def test_streams_and_cites_with_context():
    out = "".join(
        [t async for t in synthesize.synthesize_stream("¿cuánto dura?", [_chunk()], llm=FakeLLM())]
    )
    assert "[1]" in out


def test_build_sources_numbering():
    sources = synthesize.build_sources([_chunk()])
    assert sources == [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}]
