import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app import db
from app.eval import baseline as _baseline
from app.eval.cases import EvalCase, load_golden_set
from app.eval.checks import deterministic_failures, execution_accuracy
from app.eval.fixtures import ensure_rag_fixture
from app.eval.harness import run_case
from app.eval.metrics import MetricScores, RagSample, score_rag_cases

LAST_RUN_PATH = Path(__file__).with_name("last_run.json")


@dataclass
class CaseOutcome:
    question: str
    category: str
    failures: list[str]


@dataclass
class GateOutcome:
    case_outcomes: list[CaseOutcome]
    metrics: dict[str, float]
    regressions: list[str]
    exit_code: int


def gate_exit_code(hard_failures: int, regressions: list[str]) -> int:
    return 0 if (hard_failures == 0 and not regressions) else 1


async def _score_case(case: EvalCase) -> tuple[CaseOutcome, RagSample | None]:
    result = await run_case(case)
    failures = deterministic_failures(result)
    if case.category == "sql" and not failures and case.gold_sql:
        if not await execution_accuracy(case.gold_sql, result.candidate_sql):
            failures.append("execution-accuracy: result set != gold")
    sample: RagSample | None = None
    if case.expected_behavior == "cited_answer" and case.ground_truth:
        sample = RagSample(
            question=case.question,
            answer=result.answer,
            contexts=result.retrieved,
            ground_truth=case.ground_truth,
        )
    return CaseOutcome(question=case.question, category=case.category, failures=failures), sample


async def evaluate_gate(
    only: str | None = None, tolerance: float = 0.05, update_baseline: bool = False
) -> GateOutcome:
    cases = load_golden_set()
    if only:
        cases = [c for c in cases if c.category == only]

    outcomes: list[CaseOutcome] = []
    samples: list[RagSample] = []
    try:
        # el gate garantiza su fixture RAG (self-heal del wipe de Qdrant por la suite)
        await ensure_rag_fixture()
        for case in cases:
            outcome, sample = await _score_case(case)
            outcomes.append(outcome)
            if sample is not None:
                samples.append(sample)
    finally:
        await db.close_pool()

    metrics: dict[str, float] = {}
    if samples:
        agg: MetricScores = await score_rag_cases(samples)
        metrics["faithfulness"] = agg.faithfulness
        metrics["answer_relevancy"] = agg.answer_relevancy
        metrics["context_precision"] = agg.context_precision
        metrics["context_recall"] = agg.context_recall

    sql_outcomes = [o for o in outcomes if o.category == "sql"]
    if sql_outcomes:
        passed = sum(1 for o in sql_outcomes if not o.failures)
        metrics["execution_accuracy"] = passed / len(sql_outcomes)

    base = _baseline.load_baseline()
    compare_base = base
    if base is not None and only is not None:
        # Corrida parcial (--only): comparar SOLO las familias de métrica que este modo
        # produce; si no, las familias ausentes se leen como 0.0 y disparan falsas regresiones.
        compare_base = {k: v for k, v in base.items() if k in metrics}
    regs = _baseline.regressions(compare_base, metrics, tolerance)
    hard = sum(1 for o in outcomes if o.failures)
    exit_code = gate_exit_code(hard, regs)

    LAST_RUN_PATH.write_text(
        json.dumps(
            {
                "metrics": metrics,
                "regressions": regs,
                "cases": [{"question": o.question, "failures": o.failures} for o in outcomes],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if update_baseline:
        _baseline.save_baseline(metrics)

    return GateOutcome(
        case_outcomes=outcomes, metrics=metrics, regressions=regs, exit_code=exit_code
    )


def _print_report(outcome: GateOutcome, had_baseline: bool) -> None:
    print("== Praxia eval gate ==")
    for o in outcome.case_outcomes:
        mark = "PASS" if not o.failures else "FAIL"
        print(f"[{mark}] {o.question}")
        for failure in o.failures:
            print(f"        - {failure}")
    print("-- metricas --")
    for key, value in outcome.metrics.items():
        print(f"  {key}: {value:.3f}")
    if not had_baseline:
        print("(sin baseline; corre con --update-baseline para fijar la linea base)")
    for reg in outcome.regressions:
        print(f"  REGRESION {reg}")
    print(f"exit={outcome.exit_code}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Suite de eval offline de Praxia (gate).")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--only", choices=["rag", "sql"], default=None)
    parser.add_argument("--tolerance", type=float, default=0.05)
    args = parser.parse_args()

    had_baseline = _baseline.load_baseline() is not None
    outcome = asyncio.run(
        evaluate_gate(
            only=args.only, tolerance=args.tolerance, update_baseline=args.update_baseline
        )
    )
    _print_report(outcome, had_baseline)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
