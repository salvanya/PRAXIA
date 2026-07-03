from collections.abc import AsyncIterator
from typing import Any

from app.config import get_settings
from app.context import format_memories_block
from app.models import Chunk

ABSTAIN_MESSAGE = "No encuentro esa información en los documentos disponibles."

SYSTEM_PROMPT = (
    "Sos el asistente de una práctica profesional. Respondé en español SOLO con la "
    "información de los fragmentos provistos. Citá las fuentes que uses con la marca [n]. "
    "Si la respuesta no está en los fragmentos, respondé exactamente: "
    f"'{ABSTAIN_MESSAGE}'. No inventes ni uses conocimiento externo."
)


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
    if not chunks:
        yield ABSTAIN_MESSAGE
        return
    llm = llm or _default_llm()
    messages: list[tuple[str, str]] = [("system", SYSTEM_PROMPT)]
    block = format_memories_block(memories or [])
    if block:
        messages.append(("system", block))
    messages.append(("human", f"Fragmentos:\n\n{_format_context(chunks)}\n\nPregunta: {query}"))
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
