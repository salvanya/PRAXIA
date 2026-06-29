from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app import db
from app.agents.action_agent import ProposalResult, propose_appointment
from app.agents.cancel_agent import propose_cancellation
from app.agents.interaction_agent import propose_interaction
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
}


WRITE_KINDS: tuple[str, ...] = (
    "create_appointment",
    "log_interaction",
    "cancel_appointment",
    "unsupported",
)

CLASSIFY_PROMPT = (
    "Sos el despachador de acciones de escritura de un CRM de prácticas profesionales. "
    "El usuario pidió ejecutar UNA acción que modifica datos. Clasificá QUÉ acción es:\n"
    "- create_appointment: agendar/crear un turno NUEVO. "
    'Ej: "agendá un turno para Ana mañana 10", "dale una cita a Juan el martes", '
    '"reservá un turno con la Dra. Gómez".\n'
    "- log_interaction: registrar/anotar una interacción YA OCURRIDA con un cliente "
    "(sesión, llamada, email, nota, mensaje). "
    'Ej: "registrá que llamé a Ana", "anotá una nota sobre Juan".\n'
    "- cancel_appointment: cancelar/anular un turno YA EXISTENTE. "
    'Ej: "cancelá el turno de Juan", "anulá la cita de Ana del martes", '
    '"cancelá el turno de las 10 de Pedro".\n'
    "- unsupported: cualquier OTRA acción de escritura que NO sea esas tres "
    "(REPROGRAMAR/EDITAR un turno, dar de baja un cliente, facturar). "
    'Ej: "reprogramá el turno de Juan", "cambiá la hora de la cita".\n'
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
