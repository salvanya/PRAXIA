import asyncio
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.config import get_settings
from app.graph.state import AgentState, last_user_text
from app.memory import long_term, reflect, summarize

logger = logging.getLogger(__name__)


def _last_ai_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _to_role_text(messages: list[Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in messages:
        text = getattr(m, "content", "")
        if isinstance(text, str) and text:
            out.append(("human" if isinstance(m, HumanMessage) else "ai", text))
    return out


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
        except Exception:  # noqa: BLE001 - touch es side-effect no esencial
            logger.warning("recall_node: touch_last_used falló (best-effort)", exc_info=True)
    return {"memories": memories}


async def _reflect_delta(state: AgentState) -> dict:
    """Memoria de largo plazo (gate → extract → store). Best-effort; no aporta delta de state."""
    try:
        await reflect.run(
            state["practice_id"], last_user_text(state), _last_ai_text(state["messages"])
        )
    except Exception:  # noqa: BLE001 - best-effort (reflect.run ya es best-effort; doble guarda)
        logger.warning("consolidate: reflect best-effort falló", exc_info=True)
    return {}


async def _summary_delta(state: AgentState) -> dict:
    """Update incremental del running_summary. Solo con desalojo; best-effort + time-boxed."""
    s = get_settings()
    if not s.summary_enabled:
        return {}
    msgs = state["messages"]
    evict_upto = len(msgs) - s.short_term_history_window
    already = state.get("summarized_count", 0)
    if evict_upto <= already:
        return {}
    newly = _to_role_text(msgs[already:evict_upto])
    if not newly:
        return {}
    try:
        new_summary = await asyncio.wait_for(
            summarize.run(state.get("running_summary", ""), newly), timeout=s.summary_timeout_s
        )
    except Exception:  # noqa: BLE001 - best-effort: timeout/fallo conserva el summary previo
        logger.warning("consolidate: summary best-effort falló", exc_info=True)
        return {}
    if not new_summary:
        return {}
    return {"running_summary": new_summary, "summarized_count": evict_upto}


async def consolidate_node(state: AgentState) -> dict:
    """Cierre de turno: memoria LP (reflect) + running_summary, CONCURRENTES y best-effort.
    Ninguna de las dos ramas puede romper el turno; solo el summary aporta delta de state."""
    deltas = await asyncio.gather(
        _reflect_delta(state), _summary_delta(state), return_exceptions=True
    )
    merged: dict = {}
    for d in deltas:
        if isinstance(d, dict):
            merged.update(d)
    return merged
