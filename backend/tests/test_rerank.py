import pytest

from app.models import Chunk
from app.rag import rerank


def _c(text: str, doc_id: str) -> Chunk:
    return Chunk(text=text, page=None, chunk_index=0, document_id=doc_id, title="T", doc_type="x")


class FakeCE:
    def __init__(self, scores: list[float]):
        self._scores = scores

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return self._scores


class BoomCE:
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        raise RuntimeError("model down")


async def test_rerank_orders_by_score_desc(monkeypatch):
    chunks = [_c("a", "1"), _c("b", "2"), _c("c", "3")]
    monkeypatch.setattr(rerank, "_model", lambda: FakeCE([0.30, 0.95, 0.60]))
    out = await rerank.rerank("q", chunks)
    assert [c["document_id"] for c in out] == ["2", "3", "1"]


async def test_rerank_drops_below_floor(monkeypatch):
    chunks = [_c("a", "1"), _c("b", "2")]
    # predict() ya devuelve prob en [0,1]: 0.9 (keep) ; 0.05 (< 0.2 floor, drop)
    monkeypatch.setattr(rerank, "_model", lambda: FakeCE([0.9, 0.05]))
    out = await rerank.rerank("q", chunks)
    assert [c["document_id"] for c in out] == ["1"]


async def test_rerank_caps_to_top_k(monkeypatch):
    chunks = [_c(str(i), str(i)) for i in range(10)]
    monkeypatch.setattr(rerank, "_model", lambda: FakeCE([i / 10 for i in range(10)]))
    out = await rerank.rerank("q", chunks)
    assert len(out) == 5
    assert [c["document_id"] for c in out] == ["9", "8", "7", "6", "5"]


async def test_rerank_falls_back_to_dense_order_on_error(monkeypatch):
    chunks = [_c("a", "1"), _c("b", "2")]
    monkeypatch.setattr(rerank, "_model", lambda: BoomCE())
    out = await rerank.rerank("q", chunks)
    assert [c["document_id"] for c in out] == ["1", "2"]


async def test_rerank_empty_returns_empty():
    assert await rerank.rerank("q", []) == []


@pytest.mark.llm
async def test_real_reranker_returns_probabilities_floor_drops_irrelevant():
    """Regresion doble-sigmoid: CrossEncoder(bge-reranker-v2-m3) ya aplica Sigmoid,
    asi que predict() devuelve probabilidades en [0,1]. rerank() NO debe re-sigmoidear
    (eso comprimiria todo a [0.5, 0.73] y anularia el floor). Con el modelo real, un
    chunk claramente irrelevante (prob << 0.2) cae por el floor. Requiere los pesos de
    bge-reranker-v2-m3 (descarga 1a vez ~600MB)."""
    relevant = _c("La primera consulta dura 60 minutos.", "rel")
    irrelevant = _c("El cielo es azul y los gatos maullan.", "irr")
    out = await rerank.rerank("cuanto dura la primera consulta", [relevant, irrelevant])
    ids = [c["document_id"] for c in out]
    assert "rel" in ids
    assert "irr" not in ids
