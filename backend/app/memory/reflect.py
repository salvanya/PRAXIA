import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel

from app.config import get_settings
from app.memory import long_term

logger = logging.getLogger(__name__)


class GateVerdict(BaseModel):
    worth_remembering: bool
    is_explicit: bool
    reason: str


class MemoryCandidate(BaseModel):
    kind: Literal["preferencia", "hecho", "episodica"]
    content: str


class ExtractedMemories(BaseModel):
    memories: list[MemoryCandidate]


GATE_PROMPT = (
    "Sos un filtro de memoria de un CRM de prácticas profesionales. Dado el último turno "
    "(usuario + asistente), decidí si hay un hecho o preferencia DURADERO y a nivel PRÁCTICA "
    "que valga la pena recordar (glosario/terminología, reglas de agenda, políticas, duración "
    "de turnos, nombres del equipo). worth_remembering=true SOLO en ese caso. "
    "false para: saludos, charla trivial, preguntas puntuales, contexto efímero, y CUALQUIER "
    "dato de un cliente/paciente específico o con datos personales (fuera de alcance). "
    "Ante la duda, false. is_explicit=true si el usuario pidió recordarlo ('acordate que…', "
    "'recordá que…', 'tené en cuenta que…')."
)

EXTRACT_PROMPT = (
    "Extraé los hechos/preferencias DURADEROS de la práctica del último turno, como memorias "
    "atómicas y autocontenidas (sin pronombres ni dependencias de contexto), normalizadas, en "
    "español, de ≤200 caracteres. Ej: 'Los turnos de seguimiento duran 30 minutos.'. "
    "kind: 'preferencia' (cómo quieren las cosas), 'hecho' (dato objetivo), "
    "'episodica' (algo puntual). "
    "Si no hay nada duradero, devolvé una lista vacía."
)


def _cheap_llm() -> Any:
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model_cheap, temperature=0.0)


async def _structured(model: type[BaseModel], messages: list[tuple[str, str]]) -> Any:
    """Structured output en e4b con reintento ante el None intermitente (gotcha Gemma)."""
    bound = _cheap_llm().with_structured_output(model)
    for _ in range(2):
        try:
            out = await bound.ainvoke(messages)
        except Exception:  # noqa: BLE001 - cualquier fallo cuenta como intento
            out = None
        if out is not None:
            return out
    return None


def _turn(user_text: str, assistant_text: str) -> str:
    return f"Usuario: {user_text}\nAsistente: {assistant_text}"


async def gate(user_text: str, assistant_text: str) -> GateVerdict | None:
    return await _structured(
        GateVerdict, [("system", GATE_PROMPT), ("human", _turn(user_text, assistant_text))]
    )


async def extract(user_text: str, assistant_text: str) -> list[MemoryCandidate]:
    out = await _structured(
        ExtractedMemories, [("system", EXTRACT_PROMPT), ("human", _turn(user_text, assistant_text))]
    )
    if out is None:
        return []
    return out.memories[: get_settings().memory_reflect_max_candidates]


async def _reflect(practice_id: str, user_text: str, assistant_text: str) -> None:
    verdict = await gate(user_text, assistant_text)
    if verdict is None or not verdict.worth_remembering:
        return
    source = "explicito" if verdict.is_explicit else "reflexion"
    salience = 0.8 if verdict.is_explicit else 0.5
    for candidate in await extract(user_text, assistant_text):
        await long_term.store(
            practice_id,
            kind=candidate.kind,
            content=candidate.content,
            source=source,
            salience=salience,
        )


async def run(practice_id: str, user_text: str, assistant_text: str) -> None:
    """Best-effort: gate → extract → store, con timeout. NUNCA levanta (no rompe el turno)."""
    s = get_settings()
    if not s.memory_reflect_enabled or not user_text or not assistant_text:
        return
    try:
        await asyncio.wait_for(
            _reflect(practice_id, user_text, assistant_text), timeout=s.memory_reflect_timeout_s
        )
    except Exception:  # noqa: BLE001 - best-effort: cualquier fallo se loguea y se ignora
        logger.warning("reflect best-effort falló", exc_info=True)
