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


def _default_llm() -> Any:
    from langchain_ollama import ChatOllama

    s = get_settings()
    return ChatOllama(model=s.ollama_model, base_url=s.ollama_base_url, temperature=0.1)


async def synthesize_stream(query: str, chunks: list[Chunk], llm: Any = None) -> AsyncIterator[str]:
    if not chunks:
        yield ABSTAIN_MESSAGE
        return
    llm = llm or _default_llm()
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", f"Fragmentos:\n\n{_format_context(chunks)}\n\nPregunta: {query}"),
    ]
    async for piece in llm.astream(messages):
        text = getattr(piece, "content", "")
        if text:
            yield text
