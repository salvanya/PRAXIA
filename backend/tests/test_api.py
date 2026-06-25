import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.rag import synthesize


class FakeChunkMsg:
    def __init__(self, content: str):
        self.content = content


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    class FakeLLM:
        async def astream(self, messages):
            for token in ["Respuesta ", "citada ", "[1]."]:
                yield FakeChunkMsg(token)

    monkeypatch.setattr(synthesize, "_default_llm", lambda: FakeLLM())
    # El router e4b no debe llamar a Ollama en tests no-LLM: forzamos intent rag.
    from app.graph import router

    async def fake_classify(*_args, **_kwargs):
        return "rag"

    monkeypatch.setattr(router, "classify_intent", fake_classify)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health():
    async with await _client() as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.integration
async def test_ingest_then_chat_streams_sources():
    from app import vectorstore

    await vectorstore.ensure_collection()

    md = b"# Protocolo\nLa primera consulta dura 60 minutos."
    async with await _client() as c:
        ing = await c.post(
            "/ingest",
            files={"file": ("protocolo.md", md, "text/markdown")},
            data={"doc_type": "protocolo", "title": "Protocolo"},
        )
        assert ing.status_code == 200 and ing.json()["status"] == "indexado"

        async with c.stream("POST", "/chat", json={"message": "¿cuánto dura la consulta?"}) as resp:
            body = ""
            async for line in resp.aiter_lines():
                body += line + "\n"
    assert "event: token" in body
    assert "event: sources" in body


async def test_chat_returns_503_when_ollama_down(monkeypatch):
    from app import main

    async def fake_unavailable():
        return False

    monkeypatch.setattr(main, "ollama_available", fake_unavailable)

    async with await _client() as c:
        resp = await c.post("/chat", json={"message": "hola"})
    assert resp.status_code == 503
    assert "Ollama" in resp.json()["detail"]


async def test_ingest_unsupported_type():
    async with await _client() as c:
        resp = await c.post(
            "/ingest",
            files={"file": ("foto.png", b"\x89PNG", "image/png")},
            data={"doc_type": "protocolo", "title": "Foto"},
        )
    assert resp.status_code == 415
