from typing import Any

from pydantic import BaseModel

from app.llm import make_llm
from app.models import Chunk
from app.rag.synthesize import chunks_text, memories_text

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

RELEVANCE_PROMPT_WITH_MEMORY = (
    "Sos un evaluador de relevancia de un CRM para prácticas profesionales. Dada una pregunta, "
    "fragmentos de documentos y hechos que el usuario le indicó a Praxia (memoria), decidí si la "
    "COMBINACIÓN contiene información SUFICIENTE para responder. Respondé sufficient=true si la "
    "respuesta puede fundamentarse en los fragmentos O en la memoria; si ni los fragmentos ni la "
    "memoria tienen el dato, sufficient=false. Incluí una razón breve en español."
)

GROUNDEDNESS_PROMPT_WITH_MEMORY = (
    "Sos un verificador de fundamentación de un CRM para prácticas profesionales. Dada una "
    "respuesta, los fragmentos fuente y los hechos que el usuario indicó (memoria), decidí si CADA "
    "afirmación está respaldada por los fragmentos O por la memoria. grounded=true solo si todo lo "
    "afirmado se verifica en los fragmentos o en la memoria; si hay datos inventados o no "
    "presentes en ninguna fuente, grounded=false. Incluí una razón breve en español."
)


class RelevanceVerdict(BaseModel):
    sufficient: bool
    reason: str


class GroundednessVerdict(BaseModel):
    grounded: bool
    reason: str


def _judge_llm() -> Any:
    return make_llm("gemma4:e4b", temperature=0.0)


async def judge_relevance(
    query: str, chunks: list[Chunk], memories: list[dict] | None = None, llm: Any = None
) -> RelevanceVerdict:
    llm = llm or _judge_llm()
    structured = llm.with_structured_output(RelevanceVerdict)
    memories = memories or []
    if memories:
        system = RELEVANCE_PROMPT_WITH_MEMORY
        human = (
            f"Pregunta: {query}\n\nFragmentos:\n{chunks_text(chunks)}"
            f"\n\nMemoria:\n{memories_text(memories)}"
        )
    else:
        system = RELEVANCE_PROMPT
        human = f"Pregunta: {query}\n\nFragmentos:\n{chunks_text(chunks)}"
    verdict: RelevanceVerdict = await structured.ainvoke([("system", system), ("human", human)])
    return verdict


async def judge_groundedness(
    answer: str, chunks: list[Chunk], memories: list[dict] | None = None, llm: Any = None
) -> GroundednessVerdict:
    llm = llm or _judge_llm()
    structured = llm.with_structured_output(GroundednessVerdict)
    memories = memories or []
    if memories:
        system = GROUNDEDNESS_PROMPT_WITH_MEMORY
        human = (
            f"Respuesta:\n{answer}\n\nFragmentos:\n{chunks_text(chunks)}"
            f"\n\nMemoria:\n{memories_text(memories)}"
        )
    else:
        system = GROUNDEDNESS_PROMPT
        human = f"Respuesta:\n{answer}\n\nFragmentos:\n{chunks_text(chunks)}"
    verdict: GroundednessVerdict = await structured.ainvoke([("system", system), ("human", human)])
    return verdict
