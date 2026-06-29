from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app import db
from app.agents.action_agent import ProposalResult, propose_appointment
from app.agents.cancel_agent import propose_cancellation
from app.agents.interaction_agent import propose_interaction
from app.agents.reschedule_agent import propose_reschedule
from app.agents.update_client_agent import propose_update_client
from app.llm import make_llm


@dataclass(frozen=True)
class WriteTool:
    kind: str
    propose: Callable[..., Awaitable[ProposalResult]]
    write: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    format_receipt: Callable[[dict[str, Any], dict[str, Any]], str]
    cancel_message: str


# ---- create_appointment ----
async def _write_appointment(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    return await db.create_appointment(
        practice_id,
        params["client_id"],
        params["practitioner_id"],
        datetime.fromisoformat(params["start_at"]),
        datetime.fromisoformat(params["end_at"]),
        reason=params.get("reason"),
        channel=params.get("channel"),
        status=params.get("status", "programado"),
    )


def format_appointment_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    start = datetime.fromisoformat(params["start_at"])
    return (
        f"✅ Turno creado: {params['client_name']} con {params['practitioner_name']} "
        f"el {start.strftime('%d/%m %H:%M')} (UTC) (estado: {row['status']})."
    )


# ---- log_interaction ----
async def _write_interaction(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    return await db.log_interaction(
        practice_id,
        params["client_id"],
        type=params["type"],
        summary=params.get("summary"),
        content=params.get("content"),
        occurred_at=datetime.fromisoformat(params["occurred_at"]),
        source=params.get("source", "agente"),
    )


def format_interaction_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    occurred = datetime.fromisoformat(params["occurred_at"])
    return (
        f"✅ Interacción registrada: {params['type']} de {params['client_name']} "
        f"({occurred.strftime('%d/%m %H:%M')} UTC)."
    )


# ---- cancel_appointment ----
async def _write_cancel(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = await db.cancel_appointment(practice_id, params["appointment_id"])
    return {"cancelled": True, **row} if row is not None else {"cancelled": False}


def format_cancel_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("cancelled"):
        return (
            "⚠️ No pude cancelar el turno: ya no estaba disponible "
            "(puede haberse cancelado o atendido)."
        )
    start = datetime.fromisoformat(params["start_at"])
    return (
        f"✅ Turno cancelado: {params['client_name']} con {params['practitioner_name']} "
        f"el {start.strftime('%d/%m %H:%M')} (UTC)."
    )


# ---- reschedule_appointment ----
async def _write_reschedule(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    row = await db.reschedule_appointment(
        practice_id,
        params["appointment_id"],
        datetime.fromisoformat(params["new_start_at"]),
        datetime.fromisoformat(params["new_end_at"]),
    )
    return {"rescheduled": True, **row} if row is not None else {"rescheduled": False}


def format_reschedule_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("rescheduled"):
        return (
            "⚠️ No pude reprogramar el turno: ya no estaba disponible "
            "(puede haberse cancelado o atendido)."
        )
    start = datetime.fromisoformat(params["new_start_at"])
    return (
        f"✅ Turno reprogramado: {params['client_name']} con {params['practitioner_name']} "
        f"→ {start.strftime('%d/%m %H:%M')} (UTC)."
    )


# ---- update_client ----
_CLIENT_FIELD_LABELS = {
    "phone": "teléfono",
    "email": "email",
    "status": "estado",
    "dob": "fecha de nacimiento",
}


async def _write_update_client(practice_id: str, params: dict[str, Any]) -> dict[str, Any]:
    dob = params.get("dob")
    row = await db.update_client(
        practice_id,
        params["client_id"],
        phone=params.get("phone"),
        email=params.get("email"),
        status=params.get("status"),
        dob=date.fromisoformat(dob) if dob else None,
    )
    return {"updated": True, **row} if row is not None else {"updated": False}


def format_update_client_receipt(params: dict[str, Any], row: dict[str, Any]) -> str:
    if not row.get("updated"):
        return "⚠️ No pude actualizar al cliente: no lo encontré."
    campos = [
        f"{_CLIENT_FIELD_LABELS[f]} → {params[f]}"
        for f in ("phone", "email", "status", "dob")
        if params.get(f)
    ]
    return f"✅ Datos actualizados de {row['full_name']}: " + "; ".join(campos) + "."


REGISTRY: dict[str, WriteTool] = {
    "create_appointment": WriteTool(
        kind="create_appointment",
        propose=propose_appointment,
        write=_write_appointment,
        format_receipt=format_appointment_receipt,
        cancel_message="Cancelado, no creé el turno.",
    ),
    "log_interaction": WriteTool(
        kind="log_interaction",
        propose=propose_interaction,
        write=_write_interaction,
        format_receipt=format_interaction_receipt,
        cancel_message="Cancelado, no registré la interacción.",
    ),
    "cancel_appointment": WriteTool(
        kind="cancel_appointment",
        propose=propose_cancellation,
        write=_write_cancel,
        format_receipt=format_cancel_receipt,
        cancel_message="Listo, dejé el turno como estaba.",
    ),
    "reschedule_appointment": WriteTool(
        kind="reschedule_appointment",
        propose=propose_reschedule,
        write=_write_reschedule,
        format_receipt=format_reschedule_receipt,
        cancel_message="Listo, dejé el turno como estaba.",
    ),
    "update_client": WriteTool(
        kind="update_client",
        propose=propose_update_client,
        write=_write_update_client,
        format_receipt=format_update_client_receipt,
        cancel_message="Listo, no cambié los datos del cliente.",
    ),
}


WRITE_KINDS: tuple[str, ...] = (
    "create_appointment",
    "log_interaction",
    "cancel_appointment",
    "reschedule_appointment",
    "update_client",
    "unsupported",
)

CLASSIFY_PROMPT = (
    "Sos el despachador de acciones de escritura de un CRM de prácticas profesionales. "
    "El usuario pidió ejecutar UNA acción que modifica datos. Clasificá QUÉ acción es:\n"
    "- create_appointment: agendar/crear un turno NUEVO. "
    'Ej: "agendá un turno para Ana mañana 10".\n'
    "- log_interaction: registrar/anotar una interacción YA OCURRIDA con un cliente "
    "(sesión, llamada, email, nota, mensaje). "
    'Ej: "registrá que llamé a Ana".\n'
    "- cancel_appointment: cancelar/anular un turno EXISTENTE. "
    'Ej: "cancelá el turno de Juan".\n'
    "- reschedule_appointment: REPROGRAMAR/MOVER/cambiar la fecha u hora de un turno EXISTENTE "
    "(el turno sigue existiendo, cambia CUÁNDO). "
    'Ej: "reprogramá el turno de Juan para el jueves", "movés la cita de Ana a las 15", '
    '"cambiá el turno de Pedro al lunes 11".\n'
    "- update_client: editar DATOS del CLIENTE (teléfono, email, estado activo/inactivo/baja, "
    "fecha de nacimiento). "
    'Ej: "cambiá el teléfono de Ana", "actualizá el email de Juan", "dá de baja a Pedro".\n'
    "- unsupported: cualquier OTRA acción de escritura (facturar; agregar/editar una NOTA o texto "
    "libre de un cliente; borrar registros). "
    'Ej: "agregá una nota sobre Juan", "facturá la sesión de Ana".\n'
    "Respondé solo con la opción."
)


def _classify_llm() -> Any:
    return make_llm("gemma4:e4b", temperature=0.0)


async def classify_write_action(question: str, llm: Any = None) -> str:
    """Elige qué write-tool ejecutar (o 'unsupported').

    Usa ainvoke + parseo de texto en vez de with_structured_output: en Gemma local
    el structured output de e4b devuelve None de forma INTERMITENTE para ciertas
    frases de acción (mismo gotcha que el router; ver CLAUDE.md). El prompt pide
    responder solo con la opción, así que el parseo es fiable: se reintenta una vez
    y se cae a 'unsupported' (fail-closed: no abre tarjeta, no escribe) si no decide.
    """
    llm = llm or _classify_llm()
    for _ in range(2):  # reintento ante el None/respuesta vacía intermitente de e4b
        result = await llm.ainvoke([("system", CLASSIFY_PROMPT), ("human", question)])
        text = (getattr(result, "content", "") or "").strip().lower()
        if text in WRITE_KINDS:  # caso esperado: el modelo responde solo la opción
            return text
        for kind in WRITE_KINDS:  # si la envolvió en una frase, buscá la keyword
            if kind in text:
                return kind
    return "unsupported"  # fail-closed: no abre tarjeta, no escribe
