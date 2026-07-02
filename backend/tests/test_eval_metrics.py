import pytest

from app.eval.metrics import RagSample, score_rag_cases
from app.models import Chunk


@pytest.mark.eval
@pytest.mark.llm
async def test_score_rag_cases_smoke() -> None:
    ctx: Chunk = {
        "text": "La primera consulta tiene una duración de 60 minutos.",
        "page": 1,
        "chunk_index": 0,
        "document_id": "d1",
        "title": "protocolo",
        "doc_type": "protocolo",
    }
    samples = [
        RagSample(
            question="¿cuánto dura la primera consulta?",
            answer="La primera consulta dura 60 minutos.",
            contexts=[ctx],
            ground_truth="La primera consulta dura 60 minutos.",
        )
    ]
    scores = await score_rag_cases(samples)
    for value in (
        scores.faithfulness,
        scores.answer_relevancy,
        scores.context_precision,
        scores.context_recall,
    ):
        assert 0.0 <= value <= 1.0
