from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.llm import make_llm


class Choice(BaseModel):
    choice: int  # 1..n elegido; 0 si no está claro


def _system_prompt() -> str:
    return (
        "Te doy una lista NUMERADA de opciones y la respuesta de un usuario. Devolvé el NÚMERO "
        "de la opción que el usuario eligió. Si la respuesta no identifica con claridad UNA sola "
        "opción (es ambigua, vacía o cambia de tema), devolvé 0. No inventes."
    )


def _choice_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


async def resolve_choice(numbered: str, reply: str, *, n: int, gen_llm: Any = None) -> int:
    """Mapea la respuesta del usuario a un índice 1..n (0 = no claro). Fail-closed: error o
    fuera de rango → 0. El 12B con structured int es confiable (entero acotado, como un id)."""
    llm = gen_llm or _choice_llm()
    structured = llm.with_structured_output(Choice)
    human = f"Opciones:\n{numbered}\n\nRespuesta del usuario: «{reply}»"
    try:
        result = await structured.ainvoke([("system", _system_prompt()), ("human", human)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo → no-mapea
        return 0
    if not isinstance(result, Choice):
        return 0
    return result.choice if 1 <= result.choice <= n else 0
