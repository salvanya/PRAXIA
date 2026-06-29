from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.agents.action_agent import ProposalResult
from app.agents.resolvers import resolve_single_appointment, resolve_single_client
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude entender la reprogramación. ¿Me decís el cliente y la nueva fecha y hora?"
)


class ProposedReschedule(BaseModel):
    client_name: str
    current_when: str | None = None
    new_start_at: str


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt(now: datetime) -> str:
    return (
        "Sos el asistente de agenda de una práctica profesional. A partir del "
        "pedido del usuario, extraé el cliente y la REPROGRAMACIÓN de un turno "
        f"existente. La fecha y hora actuales son {now.isoformat()} (UTC). "
        "'new_start_at' es la NUEVA fecha/hora del turno (ABSOLUTA en ISO 8601; "
        "resolvé 'mañana' o 'el jueves' contra la fecha actual) y es OBLIGATORIA. "
        "'current_when' es la fecha/hora ACTUAL del turno SOLO si se menciona, "
        "para saber cuál mover (en 'del martes al jueves', current_when es el "
        "martes y new_start_at el jueves); si solo se da una fecha, esa es "
        "new_start_at y current_when es null. client_name es la persona del turno."
    )


async def _extract(question: str, now: datetime, gen_llm: Any) -> ProposedReschedule | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedReschedule)
    try:
        result = await structured.ainvoke([("system", _system_prompt(now)), ("human", question)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedReschedule) else None


def _abstain(message: str, reason: str) -> ProposalResult:
    return ProposalResult(proposed_action=None, abstained=True, message=message, reason=reason)


def _parse_when(value: str | None) -> datetime | None:
    """Pista opcional: ilegible → None (se degrada a 'sin pista')."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _card_summary(
    client_name: str, practitioner_name: str, old_start: datetime, new_start: datetime
) -> str:
    return (
        f"Reprogramar el turno de {client_name} con {practitioner_name}: "
        f"{old_start.strftime('%d/%m %H:%M')} → {new_start.strftime('%d/%m %H:%M')} (UTC)"
    )


async def propose_reschedule(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    resolution = await resolve_single_client(
        practice_id, extracted.client_name, limit=settings.appt_name_match_limit
    )
    if resolution.client is None:
        return _abstain(resolution.abstain_message, resolution.abstain_reason)
    client = resolution.client

    # new_start_at es obligatorio: si no parsea, no se puede reprogramar (no se degrada).
    try:
        new_start = datetime.fromisoformat(extracted.new_start_at)
    except ValueError:
        return _abstain(
            "No entendí la nueva fecha y hora del turno. "
            "¿Me la indicás? (p. ej. 'el jueves a las 15:00').",
            "datetime_parse_failed",
        )
    if new_start.tzinfo is None:
        new_start = new_start.replace(tzinfo=UTC)
    if new_start < now:
        return _abstain(
            "Esa fecha ya pasó. Decime una fecha y hora futura para mover el turno.",
            "new_time_past",
        )

    current_when = _parse_when(extracted.current_when)
    appt_res = await resolve_single_appointment(
        practice_id, client, current_when, now=now, limit=settings.appt_name_match_limit
    )
    if appt_res.appointment is None:
        return _abstain(appt_res.abstain_message, appt_res.abstain_reason)
    appt = appt_res.appointment
    old_start = appt["start_at"]
    new_end = new_start + (appt["end_at"] - old_start)  # preserva la duración original

    params: dict[str, Any] = {
        "appointment_id": appt["id"],
        "new_start_at": new_start.isoformat(),
        "new_end_at": new_end.isoformat(),
        "client_name": client["full_name"],
        "practitioner_name": appt["practitioner_full_name"],
        "old_start_at": old_start.isoformat(),
    }
    proposed_action = {
        "kind": "reschedule_appointment",
        "summary": _card_summary(
            client["full_name"], appt["practitioner_full_name"], old_start, new_start
        ),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
