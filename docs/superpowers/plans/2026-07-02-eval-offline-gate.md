# Suite de eval offline como gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir una suite de eval offline que corra el golden set end-to-end por el grafo real y emita un gate pass/fail (aserciones deterministas duras + métricas por LLM-as-judge local con baseline-diff).

**Architecture:** Módulos nuevos bajo `backend/app/eval/`: `cases` (tipos + loader), `harness` (corre un caso por el grafo → `CaseResult`), `checks` (aserciones deterministas + execution-accuracy), `metrics` (4 métricas por juez LLM local tras una interfaz propia), `baseline` (load/save/diff), `run` (CLI + gate). Las métricas se miden con jueces LLM locales (reusando `rag/judges.py`), **no** con Ragas (incompatible con el stack congelado de Fase 1). Todo local ($0): LM=`gemma4:12b` vía Ollama; cero dependencias nuevas.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode=auto`), LangGraph 0.2.*, langchain-ollama 0.2.*, sentence-transformers 3.*, asyncpg 0.30.*. **Sin dependencias nuevas** — métricas por LLM-as-judge local reusando `rag/judges.py`.

## Global Constraints

- **Local-first / $0:** cero red saliente nueva del producto y **cero dependencias nuevas**. Las métricas se miden con jueces LLM LOCALES (`gemma4:12b` vía Ollama), reusando `rag/judges.py`. Prohibido `ragas`/`openai` (incompatibles con el stack de Fase 1 y con el contrato).
- **Multi-tenant:** todos los casos corren con `get_settings().practice_id`; el grafo filtra por `practice_id` como siempre. No se agregan caminos que lo esquiven.
- **Gate rápido intacto:** `pytest -m "not llm"` (272) **no regresiona**. Los tests que tocan Ollama/PG/Qdrant van marcados `eval` o `llm` y quedan FUERA del gate rápido.
- **Lint/type:** `ruff format` **antes** de `ruff check` (line-length 100, reglas E/F/I/UP/B). Si `ruff check` marca **I001** (orden de imports), corré `ruff check --fix <archivos>` (autofix determinista y seguro) y volvé a chequear. `mypy backend/app --config-file backend/pyproject.toml` (`disallow_untyped_defs=true` → toda función en `app/` lleva anotaciones de tipo).
- **Marcado de tests:** los tests que tocan Ollama/PG/Qdrant llevan **`@pytest.mark.eval` Y `@pytest.mark.llm`** (el `llm` los saca del gate rápido `-m "not llm"`; el `eval` los agrupa para `-m eval`). Los tests puros (cases/checks/baseline/harness/exit-code) NO llevan marker → corren en el gate rápido.
- **Commits LIMPIOS:** sin ninguna atribución a Claude (ni trailer `Co-Authored-By`, ni firma, ni mención). Autor = el usuario. Conventional commits en español (`feat(eval):`, `test(eval):`).
- **Rama:** `fase-2/slice-eval-offline-gate` (ya creada; el spec está commiteado en `f5a1e74`).
- **Motor de métricas aislado:** solo `app/eval/metrics.py` implementa los jueces de métricas (reusando `rag/judges.py`); ninguna otra parte del código cambia por esto.

---

### Task 1: Módulo de métricas por LLM-as-judge local (`metrics.py`)

**Pivot (2026-07-02):** la librería Ragas es incompatible con el stack congelado de Fase 1 (0.2.x exige `langchain-core` 1.x; 0.1.x baja `langchain-core` a 0.2.43 + arrastra `openai`) → se descarta Ragas y se miden las 4 métricas con **jueces LLM locales** (`gemma4:12b`), reusando `rag/judges.py` para faithfulness. **Cero dependencias nuevas.** La interfaz aislada (`RagSample`/`MetricScores`/`score_rag_cases`) es la que el resto del plan consume; solo este módulo implementa los jueces de métricas.

**Files:**
- Modify: `backend/pyproject.toml` (registrar el marker `eval` — se usa por primera vez acá)
- Create: `backend/app/eval/__init__.py`
- Create: `backend/app/eval/metrics.py`
- Test: `backend/tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: `app.llm.make_llm`, `app.config.get_settings`, `app.models.Chunk`, `app.rag.judges.judge_groundedness`, `app.rag.synthesize.chunks_text`.
- Produces:
  - `RagSample(question: str, answer: str, contexts: list[Chunk], ground_truth: str)` (dataclass)
  - `MetricScores(faithfulness: float, answer_relevancy: float, context_precision: float, context_recall: float)` (dataclass)
  - `async score_rag_cases(samples: list[RagSample], llm: Any = None) -> MetricScores` (async; pega a Ollama, sin threads)

- [ ] **Step 1: Registrar el marker `eval` en `pyproject.toml`**

Se usa por primera vez en el Step 2. En `backend/pyproject.toml`, dentro de `[tool.pytest.ini_options]`, en la lista `markers`, agregar como último ítem:

```toml
    "eval: runs the full offline eval gate (needs Postgres/Qdrant + Ollama + seed demo)",
```

- [ ] **Step 2: Escribir el test smoke (falla)**

`score_rag_cases` es **async** (jueces LLM locales); el smoke es un test async marcado `eval`+`llm` (necesita Ollama; NO corre en `-m "not llm"`).

Create `backend/tests/test_eval_metrics.py`:

```python
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
```

- [ ] **Step 3: Run test para verificar que falla**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_metrics.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.eval.metrics'`.

- [ ] **Step 4: Implementar `metrics.py`**

Create `backend/app/eval/__init__.py` (vacío).

Create `backend/app/eval/metrics.py`:

```python
import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.llm import make_llm
from app.models import Chunk
from app.rag.judges import judge_groundedness
from app.rag.synthesize import chunks_text


@dataclass
class RagSample:
    question: str
    answer: str
    contexts: list[Chunk]
    ground_truth: str


@dataclass
class MetricScores:
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    context_recall: float


class _YesNo(BaseModel):
    yes: bool
    reason: str


_RELEVANCY_PROMPT = (
    "Sos un evaluador de un CRM para prácticas profesionales. Dada una PREGUNTA y una "
    "RESPUESTA, decidí si la respuesta aborda directamente lo que se preguntó. yes=true solo "
    "si responde la pregunta (no evasiva, no off-topic). Incluí una razón breve en español."
)
_PRECISION_PROMPT = (
    "Sos un evaluador de retrieval de un CRM. Dada una PREGUNTA y unos FRAGMENTOS recuperados, "
    "decidí si los fragmentos son RELEVANTES a la pregunta (no ruido off-topic). yes=true si al "
    "menos parte de los fragmentos aporta a responder la pregunta. Razón breve en español."
)
_RECALL_PROMPT = (
    "Sos un evaluador de retrieval de un CRM. Dada una RESPUESTA DE REFERENCIA y unos FRAGMENTOS "
    "recuperados, decidí si los fragmentos CONTIENEN la información necesaria para fundamentar "
    "esa respuesta de referencia. yes=true solo si la info de la referencia está en los "
    "fragmentos. Razón breve en español."
)


def _metric_llm() -> Any:
    """gemma4:12b para métricas más estables (decisión de brainstorming); el e4b sigue
    para los jueces online del grafo."""
    return make_llm(get_settings().ollama_model, temperature=0.0)


async def _judge_yes(system: str, human: str, llm: Any) -> bool:
    structured = llm.with_structured_output(_YesNo)
    verdict: _YesNo = await structured.ainvoke([("system", system), ("human", human)])
    return bool(verdict.yes)


async def _score_sample(sample: RagSample, llm: Any) -> tuple[float, float, float, float]:
    ctx = chunks_text(sample.contexts)
    grounded, relevancy, precision, recall = await asyncio.gather(
        judge_groundedness(sample.answer, sample.contexts, llm=llm),
        _judge_yes(
            _RELEVANCY_PROMPT,
            f"PREGUNTA: {sample.question}\n\nRESPUESTA:\n{sample.answer}",
            llm,
        ),
        _judge_yes(_PRECISION_PROMPT, f"PREGUNTA: {sample.question}\n\nFRAGMENTOS:\n{ctx}", llm),
        _judge_yes(
            _RECALL_PROMPT,
            f"RESPUESTA DE REFERENCIA: {sample.ground_truth}\n\nFRAGMENTOS:\n{ctx}",
            llm,
        ),
    )
    return (1.0 if grounded.grounded else 0.0, float(relevancy), float(precision), float(recall))


async def score_rag_cases(samples: list[RagSample], llm: Any = None) -> MetricScores:
    """4 métricas por juez LLM LOCAL (gemma4:12b) sobre casos RAG con respuesta grounded.
    faithfulness reusa judge_groundedness (rag/judges.py); relevancy/precision/recall son
    jueces booleanos; el score de cada métrica = promedio de los booleanos por caso."""
    if not samples:
        return MetricScores(0.0, 0.0, 0.0, 0.0)
    llm = llm or _metric_llm()
    results = await asyncio.gather(*(_score_sample(s, llm) for s in samples))
    n = len(results)
    return MetricScores(
        faithfulness=sum(r[0] for r in results) / n,
        answer_relevancy=sum(r[1] for r in results) / n,
        context_precision=sum(r[2] for r in results) / n,
        context_recall=sum(r[3] for r in results) / n,
    )
```

> Nota: los jueces usan `with_structured_output(_YesNo)` con un campo `bool` — el patrón confiable en Gemma local (structured OK para bool/enum; gotcha del router). `judge_groundedness` ya existe en `rag/judges.py` y acepta `llm=`; acá se le pasa el 12b.

- [ ] **Step 5: Run test smoke (pasa, requiere Ollama)**

Requisitos: `ollama list` muestra `gemma4:12b`. Solo necesita Ollama (sin PG/Qdrant).
Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_metrics.py -m eval -v`
Expected: PASS (4 métricas en `[0,1]`). Puede tardar (4 jueces LLM locales por caso).

- [ ] **Step 6: Lint + type + commit**

```bash
backend/.venv/Scripts/python -m ruff format backend/app/eval backend/tests/test_eval_metrics.py
backend/.venv/Scripts/python -m ruff check backend/app/eval backend/tests/test_eval_metrics.py
backend/.venv/Scripts/python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/pyproject.toml backend/app/eval/__init__.py backend/app/eval/metrics.py backend/tests/test_eval_metrics.py
git commit -m "feat(eval): metricas por LLM-as-judge local (faithfulness/relevance/precision/recall, 12b)"
```

Expected: lint/mypy limpios; commit creado.

---

### Task 2: Tipos del golden set + loader (`cases.py`) + extender `golden_set.jsonl`

**Files:**
- Create: `backend/app/eval/cases.py`
- Modify: `backend/app/eval/golden_set.jsonl` (reescribir al schema nuevo)
- Test: `backend/tests/test_eval_cases.py`

**Interfaces:**
- Consumes: `app.models.Chunk`.
- Produces:
  - `EvalCase(question, category, intent, expected_behavior, must_include, ground_truth, gold_sql, seed_doc)` (dataclass)
  - `CaseResult(case: EvalCase, intent: str, answer: str, retrieved: list[Chunk], sources: list[dict], candidate_sql: str)` (dataclass)
  - `load_golden_set(path: Path | None = None) -> list[EvalCase]`
  - `GOLDEN_SET_PATH: Path`

- [ ] **Step 1: Escribir los tests (fallan)**

Create `backend/tests/test_eval_cases.py`:

```python
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
        question="q1", category="rag", intent="rag",
        expected_behavior="cited_answer", must_include=["60"], ground_truth="dura 60",
    )
    assert cases[1].gold_sql == "SELECT 1"


def test_cited_answer_requires_ground_truth(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        ['{"question":"q","category":"rag","intent":"rag",'
         '"expected_behavior":"cited_answer","must_include":["x"]}'],
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


def test_real_golden_set_loads() -> None:
    cases = load_golden_set()  # el archivo versionado, schema nuevo
    assert len(cases) >= 4
    assert {c.category for c in cases} == {"rag", "sql"}
```

- [ ] **Step 2: Run tests para verificar que fallan**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_cases.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.eval.cases'`.

- [ ] **Step 3: Implementar `cases.py`**

Create `backend/app/eval/cases.py`:

```python
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.models import Chunk

GOLDEN_SET_PATH = Path(__file__).with_name("golden_set.jsonl")


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
```

- [ ] **Step 4: Reescribir `golden_set.jsonl` al schema nuevo**

Reemplazar TODO el contenido de `backend/app/eval/golden_set.jsonl` por (una línea JSON por caso):

```
{"question": "¿cuánto dura la primera consulta?", "category": "rag", "intent": "rag", "expected_behavior": "cited_answer", "must_include": ["60"], "ground_truth": "La primera consulta dura 60 minutos.", "seed_doc": "protocolo"}
{"question": "¿cuál es la dirección del consultorio?", "category": "rag", "intent": "rag", "expected_behavior": "abstain_no_sources", "must_include": ["No encuentro esa información"], "seed_doc": "protocolo"}
{"question": "¿cuántos turnos hay esta semana?", "category": "sql", "intent": "sql", "expected_behavior": "sql_answer", "gold_sql": "SELECT count(*) FROM appointments WHERE practice_id = '00000000-0000-0000-0000-000000000001' AND start_at >= date_trunc('week', now()) AND start_at < date_trunc('week', now()) + interval '7 days'"}
{"question": "listá los clientes activos", "category": "sql", "intent": "sql", "expected_behavior": "sql_answer", "gold_sql": "SELECT full_name FROM clients WHERE practice_id = '00000000-0000-0000-0000-000000000001' AND status = 'activo' LIMIT 200"}
```

- [ ] **Step 5: Run tests (pasan)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_cases.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Lint + type + commit**

```bash
backend/.venv/Scripts/python -m ruff format backend/app/eval/cases.py backend/tests/test_eval_cases.py
backend/.venv/Scripts/python -m ruff check backend/app/eval/cases.py backend/tests/test_eval_cases.py
backend/.venv/Scripts/python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/eval/cases.py backend/app/eval/golden_set.jsonl backend/tests/test_eval_cases.py
git commit -m "feat(eval): tipos EvalCase/CaseResult + loader del golden set (schema extendido)"
```

---

### Task 3: Aserciones deterministas + execution-accuracy (`checks.py`)

**Files:**
- Create: `backend/app/eval/checks.py`
- Test: `backend/tests/test_eval_checks.py`

**Interfaces:**
- Consumes: `app.eval.cases.CaseResult`/`EvalCase`, `app.db.run_select`, `app.config.get_settings`.
- Produces:
  - `is_select(sql: str) -> bool`
  - `deterministic_failures(result: CaseResult) -> list[str]` (puro, sin DB)
  - `result_sets_match(gold_rows: list[dict], cand_rows: list[dict]) -> bool` (puro)
  - `execution_accuracy(gold_sql: str, candidate_sql: str) -> bool` (async, usa `run_select`)

- [ ] **Step 1: Escribir los tests puros (fallan)**

Create `backend/tests/test_eval_checks.py`:

```python
from app.eval.cases import CaseResult, EvalCase
from app.eval.checks import (
    deterministic_failures,
    is_select,
    result_sets_match,
)


def _rag_cited() -> EvalCase:
    return EvalCase(
        question="q", category="rag", intent="rag",
        expected_behavior="cited_answer", must_include=["60"], ground_truth="dura 60",
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
        question="q", category="rag", intent="rag",
        expected_behavior="abstain_no_sources", must_include=["No encuentro"],
    )
    r = _result(case, answer="No encuentro esa información", sources=[{"n": 1}])
    assert any("abstain con sources" in f for f in deterministic_failures(r))


def test_sql_non_select_candidate_fails() -> None:
    case = EvalCase(
        question="q", category="sql", intent="sql",
        expected_behavior="sql_answer", gold_sql="SELECT 1",
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
```

- [ ] **Step 2: Run tests para verificar que fallan**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_checks.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.eval.checks'`.

- [ ] **Step 3: Implementar `checks.py`**

Create `backend/app/eval/checks.py`:

```python
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
```

- [ ] **Step 4: Run tests (pasan)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_checks.py -v`
Expected: PASS (10 tests). Nota: `execution_accuracy` (async/DB) se cubre en la suite `eval` (Task 6/7); acá se testea la lógica pura.

- [ ] **Step 5: Lint + type + commit**

```bash
backend/.venv/Scripts/python -m ruff format backend/app/eval/checks.py backend/tests/test_eval_checks.py
backend/.venv/Scripts/python -m ruff check backend/app/eval/checks.py backend/tests/test_eval_checks.py
backend/.venv/Scripts/python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/eval/checks.py backend/tests/test_eval_checks.py
git commit -m "feat(eval): aserciones deterministas + execution-accuracy por multiset de valores"
```

---

### Task 4: Baseline load/save/diff (`baseline.py`)

**Files:**
- Create: `backend/app/eval/baseline.py`
- Test: `backend/tests/test_eval_baseline.py`

**Interfaces:**
- Produces:
  - `load_baseline(path: Path | None = None) -> dict[str, float] | None`
  - `save_baseline(metrics: dict[str, float], path: Path | None = None) -> None`
  - `regressions(baseline: dict[str, float] | None, current: dict[str, float], tolerance: float) -> list[str]`
  - `BASELINE_PATH: Path`

- [ ] **Step 1: Escribir los tests (fallan)**

Create `backend/tests/test_eval_baseline.py`:

```python
from pathlib import Path

from app.eval.baseline import load_baseline, regressions, save_baseline


def test_load_absent_returns_none(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "nope.json") is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    save_baseline({"faithfulness": 0.9, "execution_accuracy": 1.0}, path)
    assert load_baseline(path) == {"faithfulness": 0.9, "execution_accuracy": 1.0}


def test_no_baseline_means_no_regression() -> None:
    assert regressions(None, {"faithfulness": 0.1}, tolerance=0.05) == []


def test_within_tolerance_ok() -> None:
    base = {"faithfulness": 0.90}
    assert regressions(base, {"faithfulness": 0.87}, tolerance=0.05) == []


def test_drop_beyond_tolerance_flags() -> None:
    base = {"faithfulness": 0.90}
    out = regressions(base, {"faithfulness": 0.80}, tolerance=0.05)
    assert len(out) == 1 and "faithfulness" in out[0]


def test_improvement_is_not_regression() -> None:
    base = {"faithfulness": 0.80}
    assert regressions(base, {"faithfulness": 0.95}, tolerance=0.05) == []


def test_missing_current_key_counts_as_zero() -> None:
    base = {"execution_accuracy": 1.0}
    out = regressions(base, {}, tolerance=0.05)
    assert len(out) == 1 and "execution_accuracy" in out[0]
```

- [ ] **Step 2: Run tests para verificar que fallan**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_baseline.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.eval.baseline'`.

- [ ] **Step 3: Implementar `baseline.py`**

Create `backend/app/eval/baseline.py`:

```python
import json
from pathlib import Path

BASELINE_PATH = Path(__file__).with_name("baseline.json")


def load_baseline(path: Path | None = None) -> dict[str, float] | None:
    path = path or BASELINE_PATH
    if not path.exists():
        return None
    data: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_baseline(metrics: dict[str, float], path: Path | None = None) -> None:
    path = path or BASELINE_PATH
    path.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def regressions(
    baseline: dict[str, float] | None, current: dict[str, float], tolerance: float
) -> list[str]:
    if baseline is None:
        return []
    out: list[str] = []
    for key, base in baseline.items():
        cur = current.get(key, 0.0)
        if cur < base - tolerance:
            out.append(f"{key}: {cur:.3f} < baseline {base:.3f} - tol {tolerance:.3f}")
    return out
```

- [ ] **Step 4: Run tests (pasan)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_baseline.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint + type + commit**

```bash
backend/.venv/Scripts/python -m ruff format backend/app/eval/baseline.py backend/tests/test_eval_baseline.py
backend/.venv/Scripts/python -m ruff check backend/app/eval/baseline.py backend/tests/test_eval_baseline.py
backend/.venv/Scripts/python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/eval/baseline.py backend/tests/test_eval_baseline.py
git commit -m "feat(eval): baseline load/save/diff con tolerancia (gate de regresion)"
```

---

### Task 5: Harness end-to-end (`harness.py`)

Corre un caso por el grafo real y mapea el `AgentState` final a `CaseResult`. Se testea con un grafo **mockeado** (no toca Ollama → corre en `-m "not llm"`).

**Files:**
- Create: `backend/app/eval/harness.py`
- Test: `backend/tests/test_eval_harness.py`

**Interfaces:**
- Consumes: `app.graph.build.get_default_graph`, `app.graph.state.new_state`, `app.config.get_settings`, `app.eval.cases.{EvalCase,CaseResult}`.
- Produces: `run_case(case: EvalCase, graph: Any = None) -> CaseResult`

- [ ] **Step 1: Escribir el test con grafo mockeado (falla)**

Create `backend/tests/test_eval_harness.py`:

```python
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.eval.cases import EvalCase
from app.eval.harness import run_case


class _FakeGraph:
    def __init__(self, state: dict) -> None:
        self._state = state

    async def ainvoke(self, _input: dict) -> dict:
        return self._state


async def test_run_case_maps_state() -> None:
    case = EvalCase(
        question="¿cuánto dura la primera consulta?", category="rag", intent="rag",
        expected_behavior="cited_answer", must_include=["60"], ground_truth="dura 60",
    )
    state: dict[str, Any] = {
        "intent": "rag",
        "messages": [HumanMessage("q"), AIMessage("La primera consulta dura 60 minutos.")],
        "retrieved": [],
        "sources": [{"n": 1, "title": "protocolo", "page": None, "document_id": "d1"}],
        "candidate_sql": "",
    }
    result = await run_case(case, graph=_FakeGraph(state))
    assert result.intent == "rag"
    assert result.answer == "La primera consulta dura 60 minutos."
    assert result.sources[0]["title"] == "protocolo"
    assert result.case is case
```

- [ ] **Step 2: Run test para verificar que falla**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_harness.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.eval.harness'`.

- [ ] **Step 3: Implementar `harness.py`**

Create `backend/app/eval/harness.py`:

```python
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.config import get_settings
from app.eval.cases import CaseResult, EvalCase
from app.graph.build import get_default_graph
from app.graph.state import new_state


def _last_ai_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


async def run_case(case: EvalCase, graph: Any = None) -> CaseResult:
    graph = graph or get_default_graph()
    state = await graph.ainvoke(
        new_state(case.question, get_settings().practice_id, uuid4().hex)
    )
    return CaseResult(
        case=case,
        intent=state.get("intent", ""),
        answer=_last_ai_text(state.get("messages", [])),
        retrieved=state.get("retrieved", []),
        sources=state.get("sources", []),
        candidate_sql=state.get("candidate_sql", ""),
    )
```

- [ ] **Step 4: Run test (pasa)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_harness.py -v`
Expected: PASS (grafo mockeado; sin Ollama).

- [ ] **Step 5: Lint + type + commit**

```bash
backend/.venv/Scripts/python -m ruff format backend/app/eval/harness.py backend/tests/test_eval_harness.py
backend/.venv/Scripts/python -m ruff check backend/app/eval/harness.py backend/tests/test_eval_harness.py
backend/.venv/Scripts/python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/eval/harness.py backend/tests/test_eval_harness.py
git commit -m "feat(eval): harness end-to-end (run_case -> CaseResult) via grafo real"
```

---

### Task 6: CLI + gate (`run.py`) + marker `eval` + `.gitignore` + wrapper pytest

Orquesta todo, arma el reporte, escribe `last_run.json`, decide el exit code, y expone `evaluate_gate` para el wrapper pytest (B1). Agrega `close_pool()` a `db.py` para cerrar la pool asyncpg al terminar la CLI.

**Files:**
- Modify: `backend/app/db.py` (agregar `close_pool`)
- Create: `backend/app/eval/run.py`
- Modify: `.gitignore` (ignorar `last_run.json`)
- Test: `backend/tests/test_eval_run.py` (unit del exit code; sin marker → gate rápido)
- Test: `backend/tests/test_eval_gate.py` (wrapper `@pytest.mark.eval` + `@pytest.mark.llm`)
- (El marker `eval` ya quedó registrado en `pyproject.toml` en la Task 1.)

**Interfaces:**
- Consumes: `app.eval.cases.load_golden_set`, `app.eval.harness.run_case`, `app.eval.checks.{deterministic_failures,execution_accuracy}`, `app.eval.metrics.{RagSample,score_rag_cases}`, `app.eval.baseline`, `app.db.close_pool`.
- Produces:
  - `gate_exit_code(hard_failures: int, regressions: list[str]) -> int`
  - `evaluate_gate(only: str | None = None, tolerance: float = 0.05, update_baseline: bool = False) -> GateOutcome`
  - `GateOutcome(case_outcomes: list[CaseOutcome], metrics: dict[str, float], regressions: list[str], exit_code: int)`
  - `main() -> int`

- [ ] **Step 1: Agregar `close_pool()` a `app/db.py`**

Insertar justo después de `get_pool` (después de la línea `return _pool`):

```python
async def close_pool() -> None:
    """Cierra la pool asyncpg (para procesos one-shot como la CLI de eval)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
```

- [ ] **Step 2: Escribir el test unit del exit code (falla)**

Create `backend/tests/test_eval_run.py`:

```python
from app.eval.run import gate_exit_code


def test_exit_zero_when_clean() -> None:
    assert gate_exit_code(0, []) == 0


def test_exit_one_on_hard_failure() -> None:
    assert gate_exit_code(1, []) == 1


def test_exit_one_on_regression() -> None:
    assert gate_exit_code(0, ["faithfulness: 0.8 < baseline 0.9"]) == 1
```

- [ ] **Step 3: Run test para verificar que falla**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_run.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.eval.run'`.

- [ ] **Step 4: Implementar `run.py`**

Create `backend/app/eval/run.py`:

```python
import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app import db
from app.eval import baseline as _baseline
from app.eval.cases import EvalCase, load_golden_set
from app.eval.checks import deterministic_failures, execution_accuracy
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
    regs = _baseline.regressions(base, metrics, tolerance)
    hard = sum(1 for o in outcomes if o.failures)
    exit_code = gate_exit_code(hard, regs)

    LAST_RUN_PATH.write_text(
        json.dumps(
            {
                "metrics": metrics,
                "regressions": regs,
                "cases": [
                    {"question": o.question, "failures": o.failures} for o in outcomes
                ],
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
```

- [ ] **Step 5: Run test unit (pasa)**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_eval_run.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Ignorar `last_run.json`**

Agregar la ruta al `.gitignore` de la raíz:

```bash
printf '\n# Suite de eval: artefacto efímero de la última corrida (baseline.json SÍ se commitea)\nbackend/app/eval/last_run.json\n' >> .gitignore
```

- [ ] **Step 7: Escribir el wrapper pytest `eval`**

Create `backend/tests/test_eval_gate.py`:

```python
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
```

- [ ] **Step 8: Verificar que el gate rápido no regresiona**

Run: `backend/.venv/Scripts/python -m pytest backend/tests -m "not llm" -q`
Expected: PASS; el conteo **sube desde 272** por los nuevos tests puros (cases/checks/baseline/harness/exit-code). Ningún test de la suite `eval` corre acá (los dos llevan también `llm`).

- [ ] **Step 9: Lint + type + commit**

```bash
backend/.venv/Scripts/python -m ruff format backend/app/db.py backend/app/eval/run.py backend/tests/test_eval_run.py backend/tests/test_eval_gate.py
backend/.venv/Scripts/python -m ruff check backend/app/db.py backend/app/eval/run.py backend/tests/test_eval_run.py backend/tests/test_eval_gate.py
backend/.venv/Scripts/python -m mypy backend/app --config-file backend/pyproject.toml
git add backend/app/db.py backend/app/eval/run.py .gitignore backend/tests/test_eval_run.py backend/tests/test_eval_gate.py
git commit -m "feat(eval): CLI + gate (aserciones duras + jueces baseline-diff) + wrapper pytest eval"
```

---

### Task 7: Corrida real, baseline inicial y docs

Sin código nuevo: corre el gate de punta a punta contra el entorno real, fija el baseline y documenta la invocación (CLAUDE.md está gitignored → las docs van en el repo del backend).

**Files:**
- Create: `backend/app/eval/baseline.json` (generado por la corrida; **se commitea**)
- Create: `backend/app/eval/README.md` (cómo correr el gate)

- [ ] **Step 1: Levantar el entorno**

```bash
docker compose up -d
backend/.venv/Scripts/python backend/seed_demo.py
ollama list   # confirmá gemma4:12b y gemma4:e4b
```

Expected: Postgres/Qdrant arriba; seed OK (3 prof, 30 clientes, 80 turnos); ambos modelos presentes.

- [ ] **Step 2: Primera corrida — fijar baseline**

```bash
cd backend && .venv/Scripts/python -m app.eval.run --update-baseline
```

Expected: reporte por-caso. Con `--update-baseline` no hay regresión posible (fija la línea base). **Las aserciones duras SÍ se evalúan.**

- [ ] **Step 3: Interpretar los fallos duros (si los hay)**

Si un caso falla la aserción determinista, es el gate haciendo su trabajo. Causas legítimas y su tratamiento:
- **Redacción exacta** (p. ej. la frase de abstención real difiere de `"No encuentro esa información"` por acento/mayúsculas) → **ajustá el `must_include` del caso** para que matchee el comportamiento CORRECTO del sistema (calibración de primera autoría), NO debilites la aserción.
- **Seed faltante** (el doc `protocolo` con el dato "60 min" no está sembrado) → revisá `seed_demo.py`; si el dato no existe, el caso RAG cited no aplica hasta sembrarlo.
- **Router/juez** (intención↔SQL aprueba un SELECT arbitrario; router mis-rutea) → es un hallazgo real (fast-follow fichado). Registralo; NO lo tapes bajando la vara. Si bloquea el baseline, dejá el caso y anotá el hallazgo.

Regla: **nunca** debilitar una aserción para forzar verde. Ajustar un caso para reflejar el comportamiento correcto ≠ debilitar el gate.

- [ ] **Step 4: Corrida de verificación — el gate pasa contra su propio baseline**

```bash
cd backend && .venv/Scripts/python -m app.eval.run
echo "exit=$?"
```

Expected: mismos casos PASS; sin regresión; `exit=0`.

- [ ] **Step 5: Correr el wrapper pytest `eval`**

```bash
backend/.venv/Scripts/python -m pytest backend/tests -m eval -q
```

Expected: `test_eval_gate_green` PASS + el smoke de `metrics` PASS.

- [ ] **Step 6: Escribir `backend/app/eval/README.md`**

Create `backend/app/eval/README.md`:

```markdown
# Suite de eval offline (gate de Fase 2)

Corre el golden set end-to-end por el grafo real y decide pass/fail:
- **Aserciones deterministas por-caso** (gate duro): intent, citas/abstención, `must_include`,
  y execution-accuracy del SQL (result-set gold vs candidato).
- **Métricas por LLM-as-judge local** (faithfulness / answer_relevancy / context_precision /
  context_recall, LM=`gemma4:12b`, reusando `rag/judges.py`) comparadas contra `baseline.json`
  con tolerancia.

## Correr

Requiere `docker compose up -d` + `seed_demo.py` + Ollama (`gemma4:12b` y `gemma4:e4b`).

```bash
cd backend
.venv/Scripts/python -m app.eval.run                 # corre el gate; exit 0/1
.venv/Scripts/python -m app.eval.run --update-baseline  # fija/actualiza baseline.json (commitealo)
.venv/Scripts/python -m app.eval.run --only sql         # solo casos SQL
.venv/Scripts/python -m app.eval.run --tolerance 0.1    # tolerancia del baseline-diff
```

O como test: `python -m pytest backend/tests -m eval -q`.

## Archivos
- `golden_set.jsonl` — casos (versionado; crece con cada bug arreglado).
- `baseline.json` — línea base de métricas de jueces + execution-accuracy (**se commitea**).
- `last_run.json` — resultado de la última corrida (**gitignored**).
```

- [ ] **Step 7: Commit del baseline + docs**

```bash
git add backend/app/eval/baseline.json backend/app/eval/README.md
git commit -m "feat(eval): baseline inicial + docs de la suite de eval offline"
```

Expected: `last_run.json` NO aparece en el commit (gitignored). `baseline.json` y `README.md` sí.

---

## Notas de cierre (para el review de rama / la memoria del proyecto)

- **Gates:** `-m "not llm"` sube desde 272 (nuevos tests puros: cases/checks/baseline/harness/run). Suite `eval` (nueva) = manual contra docker+Ollama. `ruff`/`mypy` limpios.
- **Invocación:** `cd backend && .venv/Scripts/python -m app.eval.run` (el path real es `app.eval.run`, NO `backend.eval.run` de CLAUDE.md §2 que era aspiracional).
- **Fast-follows destapados** (fichar en la memoria, no bloquean): crecer el golden set con cada bug; casos de acción/escritura (checkpointer + resume); Ragas como reporte histórico (se cruza con Phoenix); persistir corridas a `agent_runs`/`eval_cases`; endurecer tolerancia + umbrales absolutos; si el juez intención↔SQL aprueba SELECT arbitrarios, la execution-accuracy lo expone (arreglo = otro slice); si algún día se migra a langchain 1.x, evaluar la librería Ragas como reporte histórico (se cruza con Phoenix).
- **Pivot Ragas→jueces propios (2026-07-02):** la librería Ragas es incompatible con el stack congelado de Fase 1 (0.2.x exige `langchain-core` 1.x; 0.1.x baja core a 0.2.43 + arrastra `openai`). Se miden las 4 métricas con jueces LLM locales (12b) reusando `rag/judges.py`; `score_rag_cases` es **async** (await directo, sin `to_thread`). Cero deps nuevas.
