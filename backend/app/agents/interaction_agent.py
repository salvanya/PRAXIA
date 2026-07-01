import asyncio
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.action_agent import ProposalResult, clarify_or_abstain_client
from app.agents.resolvers import resolve_single_client
from app.config import get_settings
from app.guardrails import pii
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude registrar la interacción con esos datos. ¿Probás de nuevo indicando "
    "el cliente y qué pasó?"
)
PII_UNAVAILABLE_MESSAGE = (
    "No puedo registrar texto libre ahora mismo: el filtro de datos personales no está "
    "disponible. Avisá al administrador."
)


class ProposedInteraction(BaseModel):
    client_name: str
    type: Literal["sesion", "llamada", "email", "nota", "mensaje"] = "nota"
    summary: str
    content: str


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt() -> str:
    return (
        "Sos el asistente que registra interacciones con clientes de una práctica "
        "profesional. A partir del pedido del usuario, extraé los datos de UNA interacción "
        "ya ocurrida. Inferí 'type' de la acción mencionada: 'llamé'→llamada, 'mandé un "
        "email'→email, 'tuvimos una sesión'→sesion, 'le mandé un mensaje'→mensaje; si no es "
        "claro, usá 'nota'. Escribí 'summary' como un resumen de UNA línea y poné en 'content' "
        "el texto completo de lo que hay que registrar. 'client_name' es la persona involucrada."
    )


async def _extract(question: str, gen_llm: Any) -> ProposedInteraction | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedInteraction)
    try:
        result = await structured.ainvoke([("system", _system_prompt()), ("human", question)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedInteraction) else None


def _card_summary(client_name: str, type_: str, summary: str) -> str:
    snippet = summary.strip()
    if len(snippet) > 80:
        snippet = snippet[:79] + "…"
    return f"Registrar {type_} de {client_name} — «{snippet}»"


async def propose_interaction(
    question: str,
    practice_id: str,
    *,
    now: datetime,
    gen_llm: Any = None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,  # ignorado; uniformidad del dispatch
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, gen_llm)
    if extracted is None:
        return ProposalResult(
            proposed_action=None, abstained=True, message=GENERIC_MESSAGE, reason="extract_failed"
        )

    if client_override is not None:
        client = client_override
    else:
        resolution = await resolve_single_client(
            practice_id, extracted.client_name, limit=settings.appt_name_match_limit
        )
        if resolution.client is None:
            return clarify_or_abstain_client(resolution)
        client = resolution.client

    try:
        red_summary, _ = await asyncio.to_thread(pii.redact, extracted.summary)
        red_content, _ = await asyncio.to_thread(pii.redact, extracted.content)
    except pii.PiiUnavailable:
        return ProposalResult(
            proposed_action=None,
            abstained=True,
            message=PII_UNAVAILABLE_MESSAGE,
            reason="pii_unavailable",
        )

    params: dict[str, Any] = {
        "client_id": client["id"],
        "client_name": client["full_name"],
        "type": extracted.type,
        "summary": red_summary,
        "content": red_content,
        "occurred_at": now.isoformat(),
        "source": "agente",
    }
    proposed_action = {
        "kind": "log_interaction",
        "summary": _card_summary(client["full_name"], extracted.type, red_summary),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
