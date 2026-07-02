import pytest

from app.eval.run import evaluate_gate


@pytest.mark.eval
@pytest.mark.llm
async def test_eval_gate_green() -> None:
    """Gate en formato pytest: 0 fallos duros y 0 regresión vs baseline.
    Requiere docker (Postgres/Qdrant) + seed demo + Ollama (gemma4:12b/e4b)."""
    outcome = await evaluate_gate()
    hard = [(o.question, o.failures) for o in outcome.case_outcomes if o.failures]
    assert hard == [], f"fallos duros: {hard}"
    assert outcome.regressions == [], outcome.regressions
