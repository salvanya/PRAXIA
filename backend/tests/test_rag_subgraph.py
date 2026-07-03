from app.graph import rag_subgraph
from app.models import Chunk
from app.rag import judges
from app.rag.synthesize import ABSTAIN_MESSAGE


def _c(doc_id: str = "1") -> Chunk:
    return Chunk(
        text="La consulta dura 60 min.",
        page=None,
        chunk_index=0,
        document_id=doc_id,
        title="Protocolo",
        doc_type="protocolo",
    )


def _patch(monkeypatch, **fns):
    for name, fn in fns.items():
        monkeypatch.setattr(rag_subgraph, name, fn)


async def _ok_retrieve(query, practice_id=None, top_k=None):
    return [_c()]


async def _ok_rerank(query, chunks):
    return chunks


async def test_sufficient_first_try_returns_grounded_answer(monkeypatch):
    async def synth(q, chunks, **kwargs):
        return "La consulta dura 60 min [1]."

    async def jr(q, chunks, llm=None):
        return judges.RelevanceVerdict(sufficient=True, reason="ok")

    async def jg(a, chunks, llm=None):
        return judges.GroundednessVerdict(grounded=True, reason="ok")

    _patch(
        monkeypatch,
        retrieve=_ok_retrieve,
        rerank=_ok_rerank,
        judge_relevance=jr,
        synthesize=synth,
        judge_groundedness=jg,
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("¿cuánto dura?", "p"))
    assert out["abstained"] is False
    assert "[1]" in out["answer"]
    assert out["sources"] == [{"n": 1, "title": "Protocolo", "page": None, "document_id": "1"}]


async def test_insufficient_then_reformulate_then_sufficient(monkeypatch):
    calls = {"r": 0, "reform": 0}

    async def jr(q, chunks, llm=None):
        calls["r"] += 1
        return judges.RelevanceVerdict(sufficient=calls["r"] >= 2, reason="x")

    async def synth(q, chunks, **kwargs):
        return "ok [1]."

    async def jg(a, chunks, llm=None):
        return judges.GroundednessVerdict(grounded=True, reason="ok")

    async def reform(orig, chunks):
        calls["reform"] += 1
        return "mejor query"

    _patch(
        monkeypatch,
        retrieve=_ok_retrieve,
        rerank=_ok_rerank,
        judge_relevance=jr,
        synthesize=synth,
        judge_groundedness=jg,
        reformulate=reform,
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert calls["reform"] == 1
    assert out["abstained"] is False
    assert out["sources"]


async def test_insufficient_twice_abstains_without_sources(monkeypatch):
    retr = {"n": 0}

    async def retrieve(query, practice_id=None, top_k=None):
        retr["n"] += 1
        return [_c()]

    async def jr(q, chunks, llm=None):
        return judges.RelevanceVerdict(sufficient=False, reason="no")

    async def reform(orig, chunks):
        return "otra"

    _patch(
        monkeypatch, retrieve=retrieve, rerank=_ok_rerank, judge_relevance=jr, reformulate=reform
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert retr["n"] == 2
    assert out["abstained"] is True
    assert out["answer"] == ABSTAIN_MESSAGE
    assert out["sources"] == []


async def test_ungrounded_answer_abstains_without_sources(monkeypatch):
    async def synth(q, chunks, **kwargs):
        return "La consulta dura 90 min [1]."

    async def jr(q, chunks, llm=None):
        return judges.RelevanceVerdict(sufficient=True, reason="ok")

    async def jg(a, chunks, llm=None):
        return judges.GroundednessVerdict(grounded=False, reason="inventado")

    _patch(
        monkeypatch,
        retrieve=_ok_retrieve,
        rerank=_ok_rerank,
        judge_relevance=jr,
        synthesize=synth,
        judge_groundedness=jg,
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert out["abstained"] is True
    assert out["answer"] == ABSTAIN_MESSAGE
    assert out["sources"] == []


async def test_synth_self_abstain_skips_groundedness(monkeypatch):
    ground = {"called": False}

    async def synth(q, chunks, **kwargs):
        return ABSTAIN_MESSAGE

    async def jr(q, chunks, llm=None):
        return judges.RelevanceVerdict(sufficient=True, reason="ok")

    async def jg(a, chunks, llm=None):
        ground["called"] = True
        return judges.GroundednessVerdict(grounded=True, reason="ok")

    _patch(
        monkeypatch,
        retrieve=_ok_retrieve,
        rerank=_ok_rerank,
        judge_relevance=jr,
        synthesize=synth,
        judge_groundedness=jg,
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert ground["called"] is False
    assert out["abstained"] is True
    assert out["sources"] == []


async def test_relevance_judge_failure_is_fail_closed(monkeypatch):
    """Fail-closed: si el juez de relevancia explota, se trata como insuficiente
    (reformula/reintenta y termina en abstención sin fuentes)."""
    retr = {"n": 0}

    async def retrieve(query, practice_id=None, top_k=None):
        retr["n"] += 1
        return [_c()]

    async def jr(q, chunks, llm=None):
        raise RuntimeError("juez caido")

    async def reform(orig, chunks):
        return "otra"

    _patch(
        monkeypatch, retrieve=retrieve, rerank=_ok_rerank, judge_relevance=jr, reformulate=reform
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert retr["n"] == 2
    assert out["abstained"] is True
    assert out["sources"] == []


async def test_groundedness_judge_failure_is_fail_closed(monkeypatch):
    """Fail-closed: si el juez de groundedness explota, se trata como NO fundamentado
    → abstención sin fuentes (no se filtra la respuesta sin verificar)."""

    async def synth(q, chunks, **kwargs):
        return "La consulta dura 60 min [1]."

    async def jr(q, chunks, llm=None):
        return judges.RelevanceVerdict(sufficient=True, reason="ok")

    async def jg(a, chunks, llm=None):
        raise RuntimeError("juez caido")

    _patch(
        monkeypatch,
        retrieve=_ok_retrieve,
        rerank=_ok_rerank,
        judge_relevance=jr,
        synthesize=synth,
        judge_groundedness=jg,
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert out["abstained"] is True
    assert out["answer"] == ABSTAIN_MESSAGE
    assert out["sources"] == []


async def test_empty_rerank_is_insufficient_without_calling_judge(monkeypatch):
    """Sin chunks tras rerank, grade marca insuficiente SIN invocar al juez."""
    calls = {"jr": 0}

    async def empty_rerank(query, chunks):
        return []

    async def jr(q, chunks, llm=None):
        calls["jr"] += 1
        return judges.RelevanceVerdict(sufficient=True, reason="no deberia llamarse")

    async def reform(orig, chunks):
        return "otra"

    _patch(
        monkeypatch,
        retrieve=_ok_retrieve,
        rerank=empty_rerank,
        judge_relevance=jr,
        reformulate=reform,
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert calls["jr"] == 0
    assert out["abstained"] is True
    assert out["sources"] == []


async def test_empty_synthesis_abstains_without_sources(monkeypatch):
    """Si la síntesis sale vacía, se auto-abstiene SIN llamar a groundedness ni emitir fuentes."""
    ground = {"called": False}

    async def synth(q, chunks, **kwargs):
        return "   "

    async def jr(q, chunks, llm=None):
        return judges.RelevanceVerdict(sufficient=True, reason="ok")

    async def jg(a, chunks, llm=None):
        ground["called"] = True
        return judges.GroundednessVerdict(grounded=True, reason="ok")

    _patch(
        monkeypatch,
        retrieve=_ok_retrieve,
        rerank=_ok_rerank,
        judge_relevance=jr,
        synthesize=synth,
        judge_groundedness=jg,
    )
    out = await rag_subgraph.crag_app.ainvoke(rag_subgraph.initial_rag_state("q", "p"))
    assert ground["called"] is False
    assert out["abstained"] is True
    assert out["sources"] == []
