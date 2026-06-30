from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from app import db
from app.agents.action_agent import ProposalResult, clarify_or_abstain_client
from app.agents.resolvers import resolve_single_client
from app.config import get_settings
from app.llm import make_llm

GENERIC_MESSAGE = (
    "No pude entender qué dato del cliente cambiar. ¿Me decís el cliente y el dato "
    "(teléfono, email, estado o fecha de nacimiento)?"
)
_FIELD_LABELS = {
    "phone": "teléfono",
    "email": "email",
    "status": "estado",
    "dob": "fecha de nacimiento",
}


class ProposedClientUpdate(BaseModel):
    client_name: str
    phone: str | None = None
    email: str | None = None
    status: Literal["activo", "inactivo", "baja"] | None = None
    dob: str | None = None


def _gen_llm() -> Any:
    return make_llm(get_settings().ollama_model, temperature=0.0)


def _system_prompt() -> str:
    return (
        "Sos el asistente de datos de clientes de una práctica profesional. A partir del pedido "
        "del usuario, extraé el cliente y SOLO los datos a cambiar entre: phone (teléfono), email, "
        "status ('activo'/'inactivo'/'baja') y dob (fecha de nacimiento, formato YYYY-MM-DD). "
        "Si un dato no se menciona, dejalo en null. 'dar de baja' → status='baja'; 'reactivar' → "
        "status='activo'. No inventes valores. No extraigas notas ni texto libre."
    )


async def _extract(question: str, gen_llm: Any) -> ProposedClientUpdate | None:
    llm = gen_llm or _gen_llm()
    structured = llm.with_structured_output(ProposedClientUpdate)
    try:
        result = await structured.ainvoke([("system", _system_prompt()), ("human", question)])
    except Exception:  # noqa: BLE001 - fail-closed: cualquier fallo del LLM → abstención
        return None
    return result if isinstance(result, ProposedClientUpdate) else None


def _abstain(message: str, reason: str) -> ProposalResult:
    return ProposalResult(proposed_action=None, abstained=True, message=message, reason=reason)


def _card_summary(client_name: str, changes: dict[str, str], before: dict[str, Any]) -> str:
    parts = [
        f"{_FIELD_LABELS[field]} {before.get(field) or '—'} → {new_value}"
        for field, new_value in changes.items()
    ]
    return f"Actualizar {client_name}: " + "; ".join(parts)


async def propose_update_client(
    question: str,
    practice_id: str,
    *,
    now: datetime,
    gen_llm: Any = None,
    client_override: dict[str, Any] | None = None,
    appointment_override: dict[str, Any] | None = None,  # ignorado; uniformidad del dispatch
) -> ProposalResult:
    # `now` y `appointment_override` se aceptan por uniformidad del dispatch; no se usan acá.
    settings = get_settings()
    extracted = await _extract(question, gen_llm)
    if extracted is None:
        return _abstain(GENERIC_MESSAGE, "extract_failed")

    if client_override is not None:
        client = client_override
    else:
        resolution = await resolve_single_client(
            practice_id, extracted.client_name, limit=settings.appt_name_match_limit
        )
        if resolution.client is None:
            return clarify_or_abstain_client(resolution)
        client = resolution.client

    changes: dict[str, str] = {}
    if extracted.phone:
        changes["phone"] = extracted.phone
    if extracted.email:
        changes["email"] = extracted.email
    if extracted.status:
        changes["status"] = extracted.status
    if extracted.dob:
        try:
            date.fromisoformat(extracted.dob)
            changes["dob"] = extracted.dob
        except ValueError:
            pass  # dob ilegible → se descarta (degrada); si no queda nada, abstiene abajo
    if not changes:
        return _abstain(
            "¿Qué dato querés cambiar? Puedo teléfono, email, estado o fecha de nacimiento.",
            "no_fields",
        )

    before = await db.get_client(practice_id, client["id"]) or {}
    params: dict[str, Any] = {
        "client_id": client["id"],
        "client_name": client["full_name"],
        **changes,
    }
    proposed_action = {
        "kind": "update_client",
        "summary": _card_summary(client["full_name"], changes, before),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
