from pathlib import Path

import pytest

from app.eval.cases import EvalCase, load_golden_set


def _write(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_parses_all_fields(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            '{"question":"q1","category":"rag","intent":"rag","expected_behavior":"cited_answer",'
            '"must_include":["60"],"ground_truth":"dura 60"}',
            '{"question":"q2","category":"sql","intent":"sql","expected_behavior":"sql_answer",'
            '"gold_sql":"SELECT 1"}',
        ],
    )
    cases = load_golden_set(path)
    assert len(cases) == 2
    assert cases[0] == EvalCase(
        question="q1",
        category="rag",
        intent="rag",
        expected_behavior="cited_answer",
        must_include=["60"],
        ground_truth="dura 60",
    )
    assert cases[1].gold_sql == "SELECT 1"


def test_cited_answer_requires_ground_truth(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            '{"question":"q","category":"rag","intent":"rag",'
            '"expected_behavior":"cited_answer","must_include":["x"]}'
        ],
    )
    with pytest.raises(ValueError, match="ground_truth"):
        load_golden_set(path)


def test_sql_requires_gold_sql(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        ['{"question":"q","category":"sql","intent":"sql","expected_behavior":"sql_answer"}'],
    )
    with pytest.raises(ValueError, match="gold_sql"):
        load_golden_set(path)


def test_invalid_expected_behavior_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            '{"question":"q","category":"rag","intent":"rag",'
            '"expected_behavior":"cited","must_include":["x"],"ground_truth":"ref"}'
        ],
    )
    with pytest.raises(ValueError, match="expected_behavior"):
        load_golden_set(path)


def test_invalid_intent_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            '{"question":"q","category":"rag","intent":"raggg",'
            '"expected_behavior":"cited_answer","must_include":["x"],"ground_truth":"ref"}'
        ],
    )
    with pytest.raises(ValueError, match="intent"):
        load_golden_set(path)


def test_real_golden_set_loads() -> None:
    cases = load_golden_set()  # el archivo versionado, schema nuevo
    assert len(cases) >= 4
    assert {c.category for c in cases} == {"rag", "sql"}


def test_memory_answer_case_validates() -> None:
    from app.eval.cases import _validate

    case = EvalCase(
        question="¿hay que dejar seña?",
        category="rag",
        intent="rag",
        expected_behavior="memory_answer",
        must_include=["5000"],
        seed_memory="Para reservar hay que dejar una seña de 5000 pesos.",
    )
    _validate(case)  # no raise


def test_memory_answer_requires_seed_memory() -> None:
    import pytest

    from app.eval.cases import _validate

    case = EvalCase(
        question="q",
        category="rag",
        intent="rag",
        expected_behavior="memory_answer",
        must_include=["x"],
        seed_memory=None,
    )
    with pytest.raises(ValueError):
        _validate(case)
