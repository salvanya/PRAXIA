from dataclasses import dataclass
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
        return ClientResolution(
            None, "¿Sobre qué cliente es? Decime el nombre.", "client_missing"
        )
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
