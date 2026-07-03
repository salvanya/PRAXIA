import logging
from typing import Any

from langchain_core.messages import AIMessage

from app.config import get_settings
from app.graph.state import AgentState, last_user_text
from app.memory import long_term, reflect

logger = logging.getLogger(__name__)


def _last_ai_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


async def recall_node(state: AgentState) -> dict:
    """Recupera memorias practice-scope por coseno y las deja en state['memories'].
    Best-effort: ante cualquier fallo devuelve [] (no rompe el turno)."""
    if not get_settings().memory_recall_enabled:
        return {"memories": []}
    try:
        memories = await long_term.recall(last_user_text(state), state["practice_id"])
    except Exception:  # noqa: BLE001 - best-effort
        logger.warning("recall_node best-effort falló (recall)", exc_info=True)
        return {"memories": []}
    if memories:
        try:
            await long_term.touch_last_used([m["id"] for m in memories])
        except Exception:  # noqa: BLE001 - touch es side-effect no esencial; no debe borrar el recall
            logger.warning("recall_node: touch_last_used falló (best-effort)", exc_info=True)
    return {"memories": memories}


async def reflect_node(state: AgentState) -> dict:
    """Reflexiona sobre el turno (gate → extract → store). No toca messages. Best-effort."""
    try:
        await reflect.run(
            state["practice_id"], last_user_text(state), _last_ai_text(state["messages"])
        )
    except Exception:  # noqa: BLE001 - best-effort (reflect.run ya es best-effort; doble guarda)
        logger.warning("reflect_node best-effort falló", exc_info=True)
    return {}
