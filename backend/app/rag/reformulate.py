from typing import Any

from pydantic import BaseModel

from app.llm import make_llm
from app.models import Chunk
from app.rag.synthesize import chunks_text

REFORMULATE_PROMPT = (
    "Sos un reformulador de consultas de búsqueda para un CRM de prácticas profesionales. La "
    "búsqueda anterior no recuperó contexto suficiente. Reescribí la pregunta del usuario como "
    "una consulta de búsqueda MEJOR en español: más específica, con sinónimos y términos del "
    "dominio (turnos, protocolo, ficha, consulta, cancelación, etc.). Devolvé solo la nueva "
    "consulta, sin explicaciones."
)


class Reformulation(BaseModel):
    query: str


def _reformulate_llm() -> Any:
    return make_llm("gemma4:e4b", temperature=0.0)


async def reformulate(original_query: str, weak_chunks: list[Chunk], llm: Any = None) -> str:
    llm = llm or _reformulate_llm()
    structured = llm.with_structured_output(Reformulation)
    human = (
        f"Pregunta original: {original_query}\n\n"
        f"Fragmentos recuperados (insuficientes):\n{chunks_text(weak_chunks)}"
    )
    result: Reformulation = await structured.ainvoke(
        [("system", REFORMULATE_PROMPT), ("human", human)]
    )
    return result.query
