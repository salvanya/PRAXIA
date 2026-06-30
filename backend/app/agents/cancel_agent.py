from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.agents.action_agent import (
    ProposalResult,
    clarify_or_abstain_appointment,
    clarify_or_abstain_client,
)
from app.agents.resolvers import resolve_single_appointment, resolve_single_client
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude identificar qué turno cancelar. " "¿Me decís el cliente y, si podés, la fecha?"
)


class ProposedCancellation(BaseModel):
    client_name: str
    when: str | None = None


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt(now: datetime) -> str:
    return (
        "Sos el asistente de agenda de una práctica profesional. A partir del pedido del usuario, "
        "extraé el cliente cuyo turno se va a CANCELAR. La fecha y hora actuales son "
        f"{now.isoformat()} (UTC). Si se menciona la fecha/hora del turno, devolvé 'when' como "
        "fecha/hora ABSOLUTA en ISO 8601 (resolvé 'mañana' o 'el martes' contra la fecha actual). "
        "Si NO se menciona la fecha, dejá 'when' en null. client_name es la persona del turno."
    )


async def _extract(question: str, now: datetime, gen_llm: Any) -> ProposedCancellation | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedCancellation)
    try:
        result = await structured.ainvoke([("system", _system_prompt(now)), ("human", question)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedCancellation) else None


def _card_summary(client_name: str, practitioner_name: str, start: datetime) -> str:
    return (
        f"Cancelar el turno de {client_name} con {practitioner_name} "
        f"el {start.strftime('%d/%m %H:%M')} (UTC)"
    )


async def propose_cancellation(
    question: str,
    practice_id: str,
    *,
    now: datetime,
    gen_llm: Any = None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return ProposalResult(
            None, abstained=True, message=GENERIC_MESSAGE, reason="extract_failed"
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

    when: datetime | None = None
    if extracted.when:
        try:
            when = datetime.fromisoformat(extracted.when)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        except ValueError:
            when = None

    if appointment_override is not None:
        appt = appointment_override
    else:
        appt_res = await resolve_single_appointment(
            practice_id, client, when, now=now, limit=settings.appt_name_match_limit
        )
        if appt_res.appointment is None:
            return clarify_or_abstain_appointment(appt_res)
        appt = appt_res.appointment
    start = appt["start_at"]

    params: dict[str, Any] = {
        "appointment_id": appt["id"],
        "client_name": client["full_name"],
        "practitioner_name": appt["practitioner_full_name"],
        "start_at": start.isoformat(),
    }
    proposed_action = {
        "kind": "cancel_appointment",
        "summary": _card_summary(client["full_name"], appt["practitioner_full_name"], start),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
