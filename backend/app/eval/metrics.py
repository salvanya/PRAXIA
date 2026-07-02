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


class YesNoVerdict(BaseModel):
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
    structured = llm.with_structured_output(YesNoVerdict)
    verdict: YesNoVerdict = await structured.ainvoke([("system", system), ("human", human)])
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
