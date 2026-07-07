from app.eval.cases import CaseResult, EvalCase
from app.eval.checks import (
    deterministic_failures,
    is_select,
    result_sets_match,
)


def _rag_cited() -> EvalCase:
    return EvalCase(
        question="q",
        category="rag",
        intent="rag",
        expected_behavior="cited_answer",
        must_include=["60"],
        ground_truth="dura 60",
    )


def _result(case: EvalCase, **kw: object) -> CaseResult:
    base = dict(intent=case.intent, answer="", retrieved=[], sources=[], candidate_sql="")
    base.update(kw)
    return CaseResult(case=case, **base)  # type: ignore[arg-type]


def test_cited_pass() -> None:
    r = _result(_rag_cited(), answer="dura 60 minutos", sources=[{"n": 1}])
    assert deterministic_failures(r) == []


def test_cited_missing_must_include() -> None:
    r = _result(_rag_cited(), answer="no dice el dato", sources=[{"n": 1}])
    assert any("falta" in f for f in deterministic_failures(r))


def test_cited_without_sources() -> None:
    r = _result(_rag_cited(), answer="dura 60", sources=[])
    assert any("sin sources" in f for f in deterministic_failures(r))


def test_intent_mismatch() -> None:
    r = _result(_rag_cited(), answer="dura 60", sources=[{"n": 1}], intent="sql")
    assert any("intent" in f for f in deterministic_failures(r))


def test_abstain_with_sources_fails() -> None:
    case = EvalCase(
        question="q",
        category="rag",
        intent="rag",
        expected_behavior="abstain_no_sources",
        must_include=["No encuentro"],
    )
    r = _result(case, answer="No encuentro esa información", sources=[{"n": 1}])
    assert any("abstain con sources" in f for f in deterministic_failures(r))


def test_sql_non_select_candidate_fails() -> None:
    case = EvalCase(
        question="q",
        category="sql",
        intent="sql",
        expected_behavior="sql_answer",
        gold_sql="SELECT 1",
    )
    r = _result(case, intent="sql", candidate_sql="DELETE FROM x")
    assert any("no es SELECT" in f for f in deterministic_failures(r))


def test_is_select() -> None:
    assert is_select("SELECT 1")
    assert is_select("  with t as (select 1) select * from t ;")
    assert not is_select("DELETE FROM x")


def test_result_sets_match_order_and_alias_insensitive() -> None:
    gold = [{"full_name": "Ana"}, {"full_name": "Beto"}]
    cand = [{"name": "Beto"}, {"name": "Ana"}]  # otro alias, otro orden
    assert result_sets_match(gold, cand)


def test_result_sets_match_scalar_count() -> None:
    assert result_sets_match([{"count": 7}], [{"n": 7}])
    assert not result_sets_match([{"count": 7}], [{"n": 8}])


def test_result_sets_mismatch_extra_row() -> None:
    assert not result_sets_match([{"x": 1}], [{"x": 1}, {"x": 2}])


def test_memory_answer_with_sources_fails() -> None:
    case = EvalCase(
        question="q",
        category="rag",
        intent="rag",
        expected_behavior="memory_answer",
        must_include=["5000"],
        seed_memory="La seña es 5000.",
    )
    result = CaseResult(
        case=case,
        intent="rag",
        answer="La seña es 5000, según me indicaste.",
        retrieved=[],
        sources=[{"n": 1, "title": "x", "page": None, "document_id": "d"}],
        candidate_sql="",
    )
    assert any("memory_answer con sources" in f for f in deterministic_failures(result))


def test_memory_answer_without_sources_passes_source_check() -> None:
    case = EvalCase(
        question="q",
        category="rag",
        intent="rag",
        expected_behavior="memory_answer",
        must_include=["5000"],
        seed_memory="La seña es 5000.",
    )
    result = CaseResult(
        case=case,
        intent="rag",
        answer="La seña es 5000, según me indicaste.",
        retrieved=[],
        sources=[],
        candidate_sql="",
    )
    assert deterministic_failures(result) == []
