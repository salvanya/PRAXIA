import json
from dataclasses import dataclass, field
from pathlib import Path

from app.models import Chunk

GOLDEN_SET_PATH = Path(__file__).with_name("golden_set.jsonl")

_BEHAVIORS = frozenset({"cited_answer", "abstain_no_sources", "sql_answer"})
_INTENTS = frozenset(
    {"rag", "sql", "action", "chitchat", "out_of_scope"}
)  # espejo de app.graph.router.INTENTS


@dataclass
class EvalCase:
    question: str
    category: str  # "rag" | "sql"
    intent: str  # esperado en state["intent"]; vocab = app.graph.router.INTENTS
    expected_behavior: str  # "cited_answer" | "abstain_no_sources" | "sql_answer"
    must_include: list[str] = field(default_factory=list)
    ground_truth: str | None = None
    gold_sql: str | None = None
    seed_doc: str | None = None


@dataclass
class CaseResult:
    case: EvalCase
    intent: str
    answer: str
    retrieved: list[Chunk]
    sources: list[dict]
    candidate_sql: str


def _validate(case: EvalCase) -> None:
    if case.category not in ("rag", "sql"):
        raise ValueError(f"category invalida {case.category!r} en {case.question!r}")
    if case.expected_behavior not in _BEHAVIORS:
        raise ValueError(
            f"expected_behavior inválido {case.expected_behavior!r} en {case.question!r}"
        )
    if case.intent not in _INTENTS:
        raise ValueError(f"intent inválido {case.intent!r} en {case.question!r}")
    if case.expected_behavior == "cited_answer":
        if not case.ground_truth:
            raise ValueError(f"cited_answer requiere ground_truth en {case.question!r}")
        if not case.must_include:
            raise ValueError(f"cited_answer requiere must_include en {case.question!r}")
    if case.category == "sql" and not case.gold_sql:
        raise ValueError(f"caso sql requiere gold_sql en {case.question!r}")


def load_golden_set(path: Path | None = None) -> list[EvalCase]:
    path = path or GOLDEN_SET_PATH
    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        case = EvalCase(
            question=raw["question"],
            category=raw["category"],
            intent=raw["intent"],
            expected_behavior=raw["expected_behavior"],
            must_include=raw.get("must_include", []),
            ground_truth=raw.get("ground_truth"),
            gold_sql=raw.get("gold_sql"),
            seed_doc=raw.get("seed_doc"),
        )
        _validate(case)
        cases.append(case)
    return cases
