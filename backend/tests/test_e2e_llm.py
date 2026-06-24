import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


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
            body = "".join([line async for line in resp.aiter_lines()])
    assert "60" in body
    assert "event: sources" in body
