from typing import Any, Literal

from pydantic import BaseModel

from app.graph.state import AgentState, last_user_text

INTENTS: tuple[str, ...] = ("rag", "sql", "action", "chitchat", "out_of_scope")

ROUTER_PROMPT = (
    "Sos el router de un CRM conversacional para prácticas profesionales (clínicas, "
    "odontología, psicología, tutorías). Clasificá el mensaje del usuario en UNA intención:\n"
    "- rag: pregunta cuya respuesta está en documentos subidos (protocolos, fichas, informes). "
    'Ej: "¿cuánto dura la primera consulta?", "¿qué dice el protocolo de cancelación?".\n'
    "- sql: pregunta sobre datos estructurados de la práctica (turnos, clientes, agenda, "
    'métricas). Ej: "¿cuántos turnos tengo esta semana?", "listá los clientes activos".\n'
    "- action: pide ejecutar una acción que modifica datos (crear/editar/cancelar). "
    'Ej: "agendá un turno para mañana", "marcá a Juan como inactivo".\n'
    "- chitchat: saludo o charla trivial sin pedido concreto. "
    'Ej: "hola", "gracias", "¿cómo estás?".\n'
    "- out_of_scope: fuera del dominio de la práctica (cocina, política, código, etc.). "
    'Ej: "¿cuál es la capital de Francia?", "escribime un poema".\n'
    "Respondé solo con la intención."
)


class RouterDecision(BaseModel):
    intent: Literal["rag", "sql", "action", "chitchat", "out_of_scope"]


def _router_llm() -> Any:
    from app.llm import make_llm

    return make_llm("gemma4:e4b", temperature=0.0)


async def classify_intent(message: str, llm: Any = None) -> str:
    llm = llm or _router_llm()
    structured = llm.with_structured_output(RouterDecision)
    decision: RouterDecision = await structured.ainvoke(
        [("system", ROUTER_PROMPT), ("human", message)]
    )
    return decision.intent


async def router_node(state: AgentState) -> dict:
    intent = await classify_intent(last_user_text(state), llm=_router_llm())
    return {"intent": intent}
