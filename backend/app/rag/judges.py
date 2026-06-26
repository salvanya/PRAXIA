from typing import Any

from pydantic import BaseModel

from app.llm import make_llm
from app.models import Chunk
from app.rag.synthesize import chunks_text

RELEVANCE_PROMPT = (
    "Sos un evaluador de relevancia de un CRM para prácticas profesionales. Dada una "
    "pregunta y un conjunto de fragmentos recuperados, decidí si los fragmentos contienen "
    "información SUFICIENTE para responder la pregunta. Respondé sufficient=true solo si la "
    "respuesta puede fundamentarse en los fragmentos; si están off-topic o falta el dato, "
    "sufficient=false. Incluí una razón breve en español."
)

GROUNDEDNESS_PROMPT = (
    "Sos un verificador de fundamentación de un CRM para prácticas profesionales. Dada una "
    "respuesta y los fragmentos fuente, decidí si CADA afirmación de la respuesta está "
    "respaldada por los fragmentos. grounded=true solo si todo lo afirmado se puede verificar "
    "en los fragmentos; si hay datos inventados o no presentes, grounded=false. Incluí una "
    "razón breve en español."
)


class RelevanceVerdict(BaseModel):
    sufficient: bool
    reason: str


class GroundednessVerdict(BaseModel):
    grounded: bool
    reason: str


def _judge_llm() -> Any:
    return make_llm("gemma4:e4b", temperature=0.0)


async def judge_relevance(query: str, chunks: list[Chunk], llm: Any = None) -> RelevanceVerdict:
    llm = llm or _judge_llm()
    structured = llm.with_structured_output(RelevanceVerdict)
    human = f"Pregunta: {query}\n\nFragmentos:\n{chunks_text(chunks)}"
    verdict: RelevanceVerdict = await structured.ainvoke(
        [("system", RELEVANCE_PROMPT), ("human", human)]
    )
    return verdict


async def judge_groundedness(
    answer: str, chunks: list[Chunk], llm: Any = None
) -> GroundednessVerdict:
    llm = llm or _judge_llm()
    structured = llm.with_structured_output(GroundednessVerdict)
    human = f"Respuesta:\n{answer}\n\nFragmentos:\n{chunks_text(chunks)}"
    verdict: GroundednessVerdict = await structured.ainvoke(
        [("system", GROUNDEDNESS_PROMPT), ("human", human)]
    )
    return verdict
