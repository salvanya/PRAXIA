import logging
from typing import Any, Literal

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from app.config import get_settings
from app.graph.nodes import chitchat_node, write_sources, write_token
from app.graph.state import AgentState, last_user_text
from app.memory import long_term

logger = logging.getLogger(__name__)


class MemoryCommand(BaseModel):
    operation: Literal["forget", "correct", "none"]
    target: str
    new_value: str


EXTRACT_COMMAND_PROMPT = (
    "El usuario le habla a un CRM. Extraé si dio una ORDEN EXPLÍCITA de gestionar lo que el "
    "asistente RECUERDA:\n"
    "- operation='forget' si ORDENA olvidar/borrar algo ('olvidá que…', 'ya no…', 'borrá de tu "
    "memoria…').\n"
    "- operation='correct' si ORDENA corregir/actualizar un dato ('corregí que…', 'corregí:…', "
    "'lo correcto es…').\n"
    "- operation='none' para TODO lo demás: saludos, preguntas, y en particular pedidos de "
    "RECORDAR algo nuevo ('acordate que…', 'tené en cuenta que…') o simples AFIRMACIONES o "
    "actualizaciones de un dato ('los turnos duran 30', 'en realidad ahora duran 45'). "
    "Ante la duda, none.\n"
    "target = el dato viejo a olvidar/corregir, en pocas palabras.\n"
    "new_value = SOLO para 'correct': el dato correcto como frase autocontenida; vacío en el resto."
)


def _cheap_llm() -> Any:
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model_cheap, temperature=0.0)


async def extract_command(text: str) -> MemoryCommand | None:
    """Extrae la operación de memoria del mensaje. None si e4b no decide (patrón router/reflect)."""
    bound = _cheap_llm().with_structured_output(MemoryCommand)
    for _ in range(2):
        try:
            out = await bound.ainvoke([("system", EXTRACT_COMMAND_PROMPT), ("human", text)])
        except Exception:  # noqa: BLE001 - cualquier fallo cuenta como intento
            out = None
        if out is not None:
            return out
    return None


async def memory_command_node(state: AgentState) -> dict:
    """Camino B: olvidá/corregí inline con eco. Self-verify (misroute → chitchat, nunca borra);
    borra solo con match confiable; ambigüedad → pide detalle. skip_reflect=True SOLO en forget
    (→ END, no re-aprende lo olvidado); correct/fallback pasan por consolidate→reflect."""
    s = get_settings()
    text = last_user_text(state)
    cmd = await extract_command(text) if s.memory_command_enabled else None
    if cmd is None or cmd.operation == "none":
        # no es un comando → chat normal; reflect DEBE correr (no perder el hecho)
        return {**await chitchat_node(state), "skip_reflect": False}

    practice_id = state["practice_id"]
    skip = cmd.operation == "forget"  # solo el olvido saltea reflect (evita re-learn)
    matches = [
        m
        for m in await long_term.recall(cmd.target, practice_id)
        if m["score"] >= s.memory_forget_min_score
    ]
    top = matches[0] if matches else None
    confident = top is not None and (top["score"] >= s.memory_dedup_threshold or len(matches) == 1)

    if top is None:
        msg = "No tengo nada guardado sobre eso."
    elif not confident:
        msg = (
            "Encontré varias cosas parecidas; "
            "decime con más detalle cuál querés que olvide o corrija."
        )
    elif cmd.operation == "forget":
        await long_term.forget(practice_id, [top["id"]])
        msg = f"Listo, me olvidé de: «{top['content']}»."
    else:  # correct
        new_value = cmd.new_value.strip()
        if not new_value:
            msg = "¿Cuál es el dato correcto? Decímelo y lo actualizo."
        else:
            await long_term.store(
                practice_id,
                kind=top["kind"],
                content=new_value,
                source="explicito",
                salience=0.8,
                supersede_ids=[top["id"]],
            )
            msg = f"Corregido. Ahora recuerdo: «{new_value}»."

    write_token(msg)
    write_sources([])
    return {"sources": [], "messages": [AIMessage(content=msg)], "skip_reflect": skip}
