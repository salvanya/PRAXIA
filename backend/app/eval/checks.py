from collections import Counter
from typing import Any

from app.config import get_settings
from app.db import run_select
from app.eval.cases import CaseResult


def is_select(sql: str) -> bool:
    norm = sql.strip().rstrip(";").lower()
    return norm.startswith("select") or norm.startswith("with")


def deterministic_failures(result: CaseResult) -> list[str]:
    case = result.case
    fails: list[str] = []
    if result.intent != case.intent:
        fails.append(f"intent {result.intent!r} != esperado {case.intent!r}")
    for needle in case.must_include:
        if needle.lower() not in result.answer.lower():
            fails.append(f"falta en la respuesta: {needle!r}")
    if case.expected_behavior == "cited_answer" and not result.sources:
        fails.append("cited_answer sin sources")
    if case.expected_behavior == "abstain_no_sources" and result.sources:
        fails.append("abstain con sources (no debería citar)")
    if case.expected_behavior == "memory_answer" and result.sources:
        fails.append("memory_answer con sources (no debería citar documentos)")
    if case.category == "sql" and not is_select(result.candidate_sql):
        fails.append(f"candidate_sql no es SELECT: {result.candidate_sql!r}")
    return fails


def _canon(rows: list[dict[str, Any]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple(sorted(str(v) for v in row.values())) for row in rows)


def result_sets_match(gold_rows: list[dict[str, Any]], cand_rows: list[dict[str, Any]]) -> bool:
    return _canon(gold_rows) == _canon(cand_rows)


async def execution_accuracy(gold_sql: str, candidate_sql: str) -> bool:
    settings = get_settings()
    gold_rows, _ = await run_select(
        gold_sql, timeout_ms=settings.sql_timeout_ms, row_limit=settings.sql_row_limit
    )
    cand_rows, _ = await run_select(
        candidate_sql, timeout_ms=settings.sql_timeout_ms, row_limit=settings.sql_row_limit
    )
    return result_sets_match(gold_rows, cand_rows)
