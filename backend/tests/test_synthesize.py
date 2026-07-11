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


async def test_synthesize_buffered_collects_stream():
    out = await synthesize.synthesize("¿cuánto dura?", [_chunk()], llm=FakeLLM())
    assert "[1]" in out


async def test_synthesize_buffered_abstains_without_context():
    out = await synthesize.synthesize("hola", [])
    assert out == synthesize.ABSTAIN_MESSAGE


async def test_memory_only_uses_memory_branch_and_does_not_abstain_guard():
    """Sin chunks pero CON memoria: NO abstiene por el guard; usa la rama con memoria."""

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    captured = {}

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("La seña es de $5000, según me indicaste.")

    out = await synthesize.synthesize(
        "¿cuánto vale la seña?",
        [],
        llm=FakeLLM(),
        memories=[{"content": "La seña vale $5000.", "kind": "hecho"}],
    )
    assert out == "La seña es de $5000, según me indicaste."
    # la memoria va en el mensaje human (no como system)
    human_texts = [m[1] for m in captured["messages"] if m[0] == "human"]
    assert any("La seña vale $5000." in t for t in human_texts)
    system_texts = [m[1] for m in captured["messages"] if m[0] == "system"]
    assert system_texts and system_texts[0] == synthesize.SYSTEM_PROMPT_WITH_MEMORY


async def test_no_memory_branch_is_byte_identical_system_and_human():
    """Invariante: sin memoria, system == SYSTEM_PROMPT y human sin sección de memoria."""

    class FakeMsg:
        def __init__(self, content):
            self.content = content

    captured = {}

    class FakeLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield FakeMsg("Según el protocolo [1].")

    await synthesize.synthesize("¿cuánto dura?", [_chunk()], llm=FakeLLM())
    assert captured["messages"][0] == ("system", synthesize.SYSTEM_PROMPT)
    human = [m[1] for m in captured["messages"] if m[0] == "human"][0]
    assert (
        human
        == f"Fragmentos:\n\n{synthesize._format_context([_chunk()])}\n\nPregunta: ¿cuánto dura?"
    )
    assert "memoria" not in human.lower()


def test_memories_text_formats_bullets():
    out = synthesize.memories_text([{"content": "A."}, {"content": "B."}])
    assert out == "- A.\n- B."


async def test_abstains_when_no_chunks_and_no_memories():
    out = await synthesize.synthesize("hola", [], memories=[])
    assert out == synthesize.ABSTAIN_MESSAGE


def test_select_sources_no_memories_returns_all():
    chunks = [_chunk()]
    assert synthesize.select_sources(chunks, "cualquier cosa", []) == synthesize.build_sources(
        chunks
    )


def test_select_sources_memory_only_answer_returns_empty():
    chunks = [_chunk()]
    # answer sin marcas [n] (respuesta desde memoria) ⇒ sin fuentes
    assert (
        synthesize.select_sources(chunks, "Según me indicaste, dura 90 min.", [{"content": "x"}])
        == []
    )


def test_select_sources_merge_returns_only_cited():
    c1 = _chunk()
    c2 = Chunk(
        text="otro", page=None, chunk_index=1, document_id="doc-2", title="Otro", doc_type="x"
    )
    out = synthesize.select_sources(
        [c1, c2], "Dura 60 [1]. Además me indicaste algo.", [{"content": "x"}]
    )
    assert out == [{"n": 1, "title": "Protocolo", "page": 2, "document_id": "doc-1"}]
