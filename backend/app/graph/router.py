from typing import Any

from app.graph.state import AgentState, last_user_text

INTENTS: tuple[str, ...] = ("rag", "sql", "action", "chitchat", "out_of_scope")

ROUTER_PROMPT = (
    "Sos el router de un CRM conversacional para prácticas profesionales (clínicas, "
    "odontología, psicología, tutorías). Clasificá el mensaje del usuario en UNA intención:\n"
    "- rag: pregunta cuya respuesta está en documentos subidos (protocolos, fichas, informes). "
    'Ej: "¿cuánto dura la primera consulta?", "¿qué dice el protocolo de cancelación?".\n'
    "- sql: pregunta sobre datos estructurados de la práctica (turnos, clientes, agenda, "
    'métricas). Ej: "¿cuántos turnos tengo esta semana?", "listá los clientes activos".\n'
    "- action: pide ejecutar una acción que modifica datos "
    "(crear, registrar/anotar, editar/cancelar). "
    'Ej: "agendá un turno para mañana", "registrá que llamé a Ana", '
    '"marcá a Juan como inactivo".\n'
    "- chitchat: saludo o charla trivial sin pedido concreto. "
    'Ej: "hola", "gracias", "¿cómo estás?".\n'
    "- out_of_scope: fuera del dominio de la práctica (cocina, política, código, etc.). "
    'Ej: "¿cuál es la capital de Francia?", "escribime un poema".\n'
    "Respondé solo con la intención."
)


def _router_llm() -> Any:
    from app.llm import make_llm

    return make_llm("gemma4:e4b", temperature=0.0)


async def classify_intent(message: str, llm: Any = None) -> str:
    """Clasifica el mensaje del usuario en una de INTENTS.

    Usa ainvoke + parseo de texto en vez de with_structured_output: en Gemma local
    el structured output del router devuelve None de forma INTERMITENTE para ciertas
    frases (gotcha documentado en CLAUDE.md; el SQL agent ya parsea texto por lo mismo).
    El prompt pide responder solo con la intención, así que el parseo es fiable: se
    reintenta una vez ante una respuesta vacía/no clara y se cae a 'chitchat' (respuesta
    segura, no toca datos) si el modelo no decide.
    """
    llm = llm or _router_llm()
    for _ in range(2):  # reintento ante el None/respuesta vacía intermitente de e4b
        result = await llm.ainvoke([("system", ROUTER_PROMPT), ("human", message)])
        text = (getattr(result, "content", "") or "").strip().lower()
        if text in INTENTS:  # caso esperado: el modelo responde solo la intención
            return text
        # los INTENTS no son substrings entre sí; el orden solo importaría si el
        # modelo devolviera varias intenciones (improbable: el prompt pide solo una)
        for intent in INTENTS:  # si la envolvió en una frase, buscá la keyword
            if intent in text:
                return intent
    return "chitchat"  # fallback seguro si el modelo no decide


async def router_node(state: AgentState) -> dict:
    intent = await classify_intent(last_user_text(state), llm=_router_llm())
    return {"intent": intent}
