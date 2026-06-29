from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from app import db


@dataclass
class ClientResolution:
    client: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str


async def resolve_single_client(practice_id: str, name: str, *, limit: int) -> ClientResolution:
    """Resuelve un nombre a un único cliente de la práctica. Fail-closed: vacío /
    no encontrado / ambiguo → sin cliente y con mensaje de abstención cordial."""
    if not name.strip():
        return ClientResolution(None, "¿Sobre qué cliente es? Decime el nombre.", "client_missing")
    clients = await db.find_clients_by_name(practice_id, name, limit=limit)
    if not clients:
        return ClientResolution(
            None,
            f"No encontré ningún cliente que coincida con «{name}». ¿Me das el nombre completo?",
            "client_not_found",
        )
    if len(clients) > 1:
        names = ", ".join(c["full_name"] for c in clients)
        return ClientResolution(
            None,
            f"Hay varios clientes que coinciden con «{name}»: {names}. ¿Cuál es?",
            "client_ambiguous",
        )
    return ClientResolution(clients[0], "", "ok")


@dataclass
class AppointmentResolution:
    appointment: dict[str, Any] | None
    abstain_message: str
    abstain_reason: str


_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _format_candidate(appt: dict[str, Any]) -> str:
    # Día en español por mapa fijo: strftime("%A") es locale-dependiente (rompe en Windows).
    start = appt["start_at"]
    day = _WEEKDAYS_ES[start.weekday()]
    return f"{day} {start.strftime('%d/%m %H:%M')} con {appt['practitioner_full_name']}"


def _format_list(appts: list[dict[str, Any]]) -> str:
    return "; ".join(_format_candidate(a) for a in appts)


async def resolve_single_appointment(
    practice_id: str,
    client: dict[str, Any],
    when: datetime | None,
    *,
    now: datetime,
    limit: int,
) -> AppointmentResolution:
    """Resuelve a UN turno cancelable del cliente. Fail-closed: 0 / ambiguo → sin turno
    + mensaje cordial. `cands` es la lista completa (para listar); `matches` es el
    subconjunto tras aplicar la pista de fecha opcional."""
    name = client["full_name"]
    cands = await db.find_cancellable_appointments(practice_id, client["id"], now=now, limit=limit)
    if not cands:
        return AppointmentResolution(
            None, f"{name} no tiene turnos próximos para cancelar.", "appointment_none"
        )
    matches = cands
    if when is not None:
        same_day = [a for a in cands if a["start_at"].date() == when.date()]
        if len(same_day) > 1 and when.time() != time(0, 0):
            timed = [
                a
                for a in same_day
                if (a["start_at"].hour, a["start_at"].minute) == (when.hour, when.minute)
            ]
            same_day = timed or same_day  # si la hora no matchea ninguno, se cae al día
        matches = same_day
    if not matches:
        return AppointmentResolution(
            None,
            f"No encontré un turno de {name} para esa fecha. "
            f"Sus próximos turnos: {_format_list(cands)}.",
            "appointment_not_found",
        )
    if len(matches) > 1:
        return AppointmentResolution(
            None,
            f"{name} tiene varios turnos próximos: {_format_list(matches)}. "
            "¿Cuál? Decime la fecha y la hora.",
            "appointment_ambiguous",
        )
    return AppointmentResolution(matches[0], "", "ok")
