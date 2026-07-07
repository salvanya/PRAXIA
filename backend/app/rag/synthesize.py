from collections.abc import AsyncIterator
from typing import Any

from app.config import get_settings
from app.models import Chunk

ABSTAIN_MESSAGE = "No encuentro esa información en los documentos disponibles."

SYSTEM_PROMPT = (
    "Sos el asistente de una práctica profesional. Respondé en español SOLO con la "
    "información de los fragmentos provistos. Citá las fuentes que uses con la marca [n]. "
    "Si la respuesta no está en los fragmentos, respondé exactamente: "
    f"'{ABSTAIN_MESSAGE}'. No inventes ni uses conocimiento externo."
)

SYSTEM_PROMPT_WITH_MEMORY = (
    "Sos el asistente de una práctica profesional. Respondé en español usando ÚNICAMENTE las "
    "fuentes provistas: los FRAGMENTOS de documentos y lo que el usuario te indicó (memoria).\n"
    "- Citá cada fragmento que uses con la marca [n].\n"
    "- Lo que te indicó el usuario NO lleva [n]; cuando lo uses, atribuílo en el texto "
    "(por ejemplo: 'según me indicaste').\n"
    "- Si algo que te indicó el usuario CONTRADICE un fragmento sobre el mismo dato, priorizá "
    "lo que te indicó el usuario (es lo más reciente) y aclará la diferencia (por ejemplo: "
    "'el protocolo indica 45 minutos, aunque me señalaste que ahora son 60').\n"
    "- Usá lo que te indicó el usuario SOLO si responde la pregunta; ignorá lo que no aplique.\n"
    "- Si NI los fragmentos NI lo que te indicó el usuario contienen la respuesta, respondé "
    f"exactamente: '{ABSTAIN_MESSAGE}'.\n"
    "No inventes ni uses conocimiento externo."
)


def memories_text(memories: list[dict]) -> str:
    """Formatea memorias como lista para el bloque de evidencia (síntesis/jueces)."""
    return "\n".join(f"- {m['content']}" for m in memories)


def _format_context(chunks: list[Chunk]) -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        page = f" — p.{c['page']}" if c["page"] is not None else ""
        blocks.append(f'[{i}] (Fuente: "{c["title"]}"{page})\n{c["text"]}')
    return "\n\n".join(blocks)


def build_sources(chunks: list[Chunk]) -> list[dict[str, Any]]:
    return [
        {"n": i, "title": c["title"], "page": c["page"], "document_id": c["document_id"]}
        for i, c in enumerate(chunks, start=1)
    ]


def select_sources(chunks: list[Chunk], answer: str, memories: list[dict]) -> list[dict[str, Any]]:
    """Fuentes a mostrar. Sin memorias: todas las reranked (comportamiento histórico).
    Con memorias: solo los chunks efectivamente citados [n] en el answer (memory-only ⇒ [])."""
    all_sources = build_sources(chunks)
    if not memories:
        return all_sources
    return [s for s in all_sources if f"[{s['n']}]" in answer]


def chunks_text(chunks: list[Chunk]) -> str:
    """Formatea chunks como lista para prompts de jueces/reformulador (sin marcas de cita)."""
    return "\n".join(f'- ({c["title"]}) {c["text"]}' for c in chunks)


async def ollama_available() -> bool:
    """Probe ligero de conectividad a Ollama (sin cargar el modelo).

    Devuelve False ante cualquier error de red/timeout para que el caller
    pueda responder un 503 amable en vez de romper a mitad del stream SSE."""
    import httpx

    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{s.ollama_base_url}/api/version")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _default_llm() -> Any:
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model, temperature=0.1)


async def synthesize_stream(
    query: str, chunks: list[Chunk], llm: Any = None, memories: list[dict] | None = None
) -> AsyncIterator[str]:
    memories = memories or []
    if not chunks and not memories:
        yield ABSTAIN_MESSAGE
        return
    llm = llm or _default_llm()
    if memories:
        system = SYSTEM_PROMPT_WITH_MEMORY
        human = (
            f"Fragmentos:\n\n{_format_context(chunks)}\n\n"
            f"Lo que me indicaste (memoria):\n{memories_text(memories)}\n\n"
            f"Pregunta: {query}"
        )
    else:
        system = SYSTEM_PROMPT
        human = f"Fragmentos:\n\n{_format_context(chunks)}\n\nPregunta: {query}"
    messages: list[tuple[str, str]] = [("system", system), ("human", human)]
    async for piece in llm.astream(messages):
        text = getattr(piece, "content", "")
        if text:
            yield text


async def synthesize(
    query: str, chunks: list[Chunk], llm: Any = None, memories: list[dict] | None = None
) -> str:
    """Variante buffered: colecta synthesize_stream a un string. Necesaria para
    buffer-then-stream — la respuesta se verifica (groundedness) antes de emitirse."""
    return "".join(
        [piece async for piece in synthesize_stream(query, chunks, llm=llm, memories=memories)]
    )
