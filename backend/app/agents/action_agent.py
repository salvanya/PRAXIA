from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from app import db
from app.agents.resolvers import AppointmentResolution, ClientResolution
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude armar el turno con esos datos. ¿Probás de nuevo indicando cliente, "
    "profesional y horario?"
)


class ProposedAppointment(BaseModel):
    client_name: str
    practitioner_name: str | None = None
    start_at: str
    duration_min: int = 30
    reason: str | None = None
    channel: Literal["presencial", "telellamada"] | None = None


@dataclass
class Clarification:
    stage: str  # "client" | "appointment"
    candidates: list[dict[str, Any]]
    prompt: str  # encabezado humano ("Hay varios clientes…" / "…tiene varios turnos…")


@dataclass
class ProposalResult:
    proposed_action: dict[str, Any] | None
    abstained: bool
    message: str
    reason: str
    clarification: Clarification | None = None


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt(now: datetime, default_duration: int) -> str:
    return (
        "Sos el asistente de agenda de una práctica profesional. A partir del pedido del "
        "usuario, extraé los datos para crear UN turno. La fecha y hora actuales son "
        f"{now.isoformat()} (UTC). Devolvé start_at como fecha/hora ABSOLUTA en ISO 8601 "
        "(resolvé expresiones como 'mañana' o 'el martes' contra la fecha actual). Si no se "
        f"menciona la duración, usá {default_duration} minutos. client_name es la persona del "
        "turno; practitioner_name SOLO si se menciona un profesional; channel SOLO si se aclara "
        "('presencial' o 'telellamada')."
    )


async def _extract(question: str, now: datetime, gen_llm: Any) -> ProposedAppointment | None:
    settings = get_settings()
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedAppointment)
    try:
        result = await structured.ainvoke(
            [
                ("system", _system_prompt(now, settings.appt_default_duration_min)),
                ("human", question),
            ]
        )
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedAppointment) else None


def _abstain(message: str, reason: str) -> ProposalResult:
    return ProposalResult(proposed_action=None, abstained=True, message=message, reason=reason)


def clarify_or_abstain_client(res: ClientResolution) -> ProposalResult:
    clar = (
        Clarification("client", res.candidates, res.abstain_message)
        if res.abstain_reason == "client_ambiguous"
        else None
    )
    return ProposalResult(
        None,
        abstained=True,
        message=res.abstain_message,
        reason=res.abstain_reason,
        clarification=clar,
    )


def clarify_or_abstain_appointment(res: AppointmentResolution) -> ProposalResult:
    clar = (
        Clarification("appointment", res.candidates, res.abstain_message)
        if res.abstain_reason == "appointment_ambiguous"
        else None
    )
    return ProposalResult(
        None,
        abstained=True,
        message=res.abstain_message,
        reason=res.abstain_reason,
        clarification=clar,
    )


def _summary(params: dict[str, Any], start: datetime, end: datetime) -> str:
    when = f"{start.strftime('%d/%m %H:%M')}–{end.strftime('%H:%M')} (UTC)"
    parts = [f"Crear turno: {params['client_name']} con {params['practitioner_name']} — {when}"]
    if params["reason"]:
        parts.append(f"motivo: {params['reason']}")
    if params["channel"]:
        parts.append(params["channel"])
    return ", ".join(parts)


async def propose_appointment(
    question: str, practice_id: str, *, now: datetime, gen_llm: Any = None
) -> ProposalResult:
    settings = get_settings()
    extracted = await _extract(question, now, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    if not extracted.client_name.strip():
        return _abstain(
            "No me dijiste para qué cliente es el turno. ¿Me pasás el nombre?",
            "client_missing",
        )

    clients = await db.find_clients_by_name(
        practice_id, extracted.client_name, limit=settings.appt_name_match_limit
    )
    if not clients:
        return _abstain(
            f"No encontré ningún cliente que coincida con «{extracted.client_name}». "
            "¿Me das el nombre completo?",
            "client_not_found",
        )
    if len(clients) > 1:
        names = ", ".join(c["full_name"] for c in clients)
        return _abstain(
            f"Hay varios clientes que coinciden con «{extracted.client_name}»: {names}. ¿Cuál es?",
            "client_ambiguous",
        )
    client = clients[0]

    if extracted.practitioner_name:
        pracs = await db.find_practitioners_by_name(
            practice_id, extracted.practitioner_name, limit=settings.appt_name_match_limit
        )
        if not pracs:
            return _abstain(
                f"No encontré ningún profesional que coincida con «{extracted.practitioner_name}».",
                "practitioner_not_found",
            )
        if len(pracs) > 1:
            names = ", ".join(p["full_name"] for p in pracs)
            return _abstain(
                f"Hay varios profesionales que coinciden con «{extracted.practitioner_name}»: "
                f"{names}. ¿Cuál?",
                "practitioner_ambiguous",
            )
        prac = pracs[0]
    else:
        pracs = await db.list_active_practitioners(practice_id)
        if not pracs:
            return _abstain(
                "No hay profesionales activos cargados en la práctica.", "no_practitioners"
            )
        if len(pracs) > 1:
            names = ", ".join(p["full_name"] for p in pracs)
            return _abstain(f"¿Con qué profesional? Tenés: {names}.", "practitioner_unspecified")
        prac = pracs[0]

    try:
        start = datetime.fromisoformat(extracted.start_at)
    except ValueError:
        return _abstain(
            "No entendí la fecha/hora del turno. ¿Me la indicás? (p. ej. 'mañana a las 10:00').",
            "datetime_parse_failed",
        )
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    duration = (
        extracted.duration_min if extracted.duration_min > 0 else settings.appt_default_duration_min
    )
    end = start + timedelta(minutes=duration)

    params: dict[str, Any] = {
        "client_id": client["id"],
        "client_name": client["full_name"],
        "practitioner_id": prac["id"],
        "practitioner_name": prac["full_name"],
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "reason": extracted.reason,
        "channel": extracted.channel,
        "status": "programado",
    }
    proposed_action = {
        "kind": "create_appointment",
        "summary": _summary(params, start, end),
        "params": params,
    }
    return ProposalResult(
        proposed_action=proposed_action,
        abstained=False,
        message="",
        reason="ok",
    )
