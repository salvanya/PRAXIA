import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _parse_sse(body: str) -> tuple[str, list[dict]]:
    """Reconstruye el texto del asistente y las fuentes desde el stream SSE.

    El modelo real tokeniza libremente (p. ej. "60" -> "6","0"), cada token en
    su propio `event: token`. Concatenar las lineas crudas parte los numeros;
    hay que unir solo los payloads `data:` de los eventos `token`, igual que el
    frontend (`lib/chatStream.ts`).
    """
    answer = ""
    sources: list[dict] = []
    event = ""
    for raw in body.split("\n"):
        if raw.startswith("event: "):
            event = raw[len("event: ") :]
        elif raw.startswith("data:"):
            payload = raw[len("data: ") :] if raw.startswith("data: ") else raw[len("data:") :]
            if event == "token":
                answer += payload
            elif event == "sources":
                sources = json.loads(payload)
    return answer, sources


@pytest.mark.llm
@pytest.mark.integration
async def test_real_llm_answers_with_citation():
    md = b"# Protocolo\nLa primera consulta dura 60 minutos y se cobra 50% por cancelacion tardia."
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/ingest",
            files={"file": ("protocolo.md", md, "text/markdown")},
            data={"doc_type": "protocolo", "title": "Protocolo"},
        )
        msg = "¿cuánto dura la primera consulta?"
        async with c.stream("POST", "/chat", json={"message": msg}) as resp:
            body = "".join([line + "\n" async for line in resp.aiter_lines()])

    answer, sources = _parse_sse(body)
    assert "60" in answer
    assert sources and sources[0]["document_id"]


@pytest.mark.llm
@pytest.mark.integration
async def test_real_llm_abstains_when_answer_not_in_context():
    """DoD #5: con fragmentos recuperados pero sin la respuesta, el modelo se abstiene."""
    md = b"# Protocolo\nLa primera consulta dura 60 minutos y se cobra 50% por cancelacion tardia."
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/ingest",
            files={"file": ("protocolo.md", md, "text/markdown")},
            data={"doc_type": "protocolo", "title": "Protocolo"},
        )
        msg = "¿cuál es la dirección del consultorio?"
        async with c.stream("POST", "/chat", json={"message": msg}) as resp:
            body = "".join([line + "\n" async for line in resp.aiter_lines()])

    answer, sources = _parse_sse(body)
    assert "No encuentro esa información" in answer
    assert sources == []
