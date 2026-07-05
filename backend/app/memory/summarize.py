import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


def _cheap_llm() -> Any:
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model_cheap, temperature=0.0)


def _system(max_words: int) -> str:
    return (
        "Mantené un resumen conciso y factual en español (tercera persona, "
        f"≤ {max_words} palabras) de una conversación. Te doy el resumen previo y "
        "turnos nuevos; devolvé SOLO el resumen actualizado, sin inventar, integrando "
        "lo nuevo, priorizando hechos, preferencias y decisiones. Sin encabezados ni comillas."
    )


def _human(old_summary: str, new_messages: list[tuple[str, str]]) -> str:
    prev = old_summary or "(vacío)"
    turns = "\n".join(
        f"{'Usuario' if role == 'human' else 'Asistente'}: {text}" for role, text in new_messages
    )
    return f"Resumen previo:\n{prev}\n\nTurnos nuevos:\n{turns}\n\nResumen actualizado:"


def _cap(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"


async def run(
    old_summary: str, new_messages: list[tuple[str, str]], *, llm: Any = None
) -> str | None:
    """Pliega los turnos nuevos sobre el resumen previo (e4b, texto plano). Devuelve el
    resumen actualizado o None (nada que plegar / e4b None tras retries). No levanta."""
    if not new_messages:
        return None
    s = get_settings()
    llm = llm or _cheap_llm()
    messages = [
        ("system", _system(s.summary_max_words)),
        ("human", _human(old_summary, new_messages)),
    ]
    for _ in range(2):  # gotcha Gemma: content vacío/None intermitente → retry
        try:
            resp = await llm.ainvoke(messages)
            text = (getattr(resp, "content", "") or "").strip()
        except Exception:  # noqa: BLE001 - cualquier fallo cuenta como intento
            text = ""
        if text:
            return _cap(text, s.summary_max_words)
    return None
