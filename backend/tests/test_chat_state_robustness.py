"""Fix 1: /chat no-clobber del estado cuando aget_state falla con checkpointer real.

Estrategia elegida: refactorización mínima + test del handler ASGI.
Usamos httpx.ASGITransport contra `app` (el mismo patrón de test_api.py) con
app.state.graph inyectado como stub. Esto prueba la rama real del handler sin
necesitar Ollama ni Postgres.

Casos:
- graph en app.state con aget_state que falla → 503 (no clobber)
- sin graph en app.state (configured=None → fallback) → flujo normal (200 / stream)
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


class _GraphStubRaises:
    """Simula un checkpointer real cuyo aget_state falla (p.ej. Postgres transitorio)."""

    async def aget_state(self, config: dict):  # type: ignore[no-untyped-def]
        raise RuntimeError("Postgres connection lost")

    async def astream(self, inp, config, *, stream_mode):  # type: ignore[no-untyped-def]
        # No debería llegar acá en el test de fallo.
        return
        yield  # hace que sea un async generator


class _GraphStubOk:
    """Simula un checkpointer real cuyo aget_state devuelve estado vacío (primer turno)."""

    class _FakeSnap:
        values: dict = {}

    async def aget_state(self, config: dict):  # type: ignore[no-untyped-def]
        return self._FakeSnap()

    async def astream(self, inp, config, *, stream_mode):  # type: ignore[no-untyped-def]
        # Emite un token mínimo y done para que el SSE no quede colgado.
        yield "custom", {"kind": "token", "text": "ok"}


@pytest.fixture()
def _fake_ollama(monkeypatch):
    """Ollama disponible para que el handler no devuelva 503 antes del aget_state."""
    from app import main

    async def _available():
        return True

    monkeypatch.setattr(main, "ollama_available", _available)


async def test_aget_state_failure_with_real_checkpointer_returns_503(
    monkeypatch, _fake_ollama
) -> None:
    """Con app.state.graph presente pero aget_state roto → 503 (no arranca limpio)."""
    app.state.graph = _GraphStubRaises()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/chat", json={"message": "hola", "thread_id": "t-rob-1"})
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
        detail = resp.json().get("detail", "")
        assert "estado" in detail or "conversación" in detail or "Reintentá" in detail
    finally:
        # Limpia para no contaminar otros tests.
        del app.state.graph


async def test_no_checkpointer_starts_clean_stream(monkeypatch, _fake_ollama) -> None:
    """Sin app.state.graph (fallback get_default_graph) → flujo normal, no 503."""
    # Aseguramos que NO hay graph en state (puede haber quedado de otro test).
    try:
        del app.state.graph
    except (AttributeError, KeyError):
        pass

    # Parcheamos get_default_graph para devolver un stub que no necesita Postgres.
    from app import main as _main

    monkeypatch.setattr(_main, "get_default_graph", lambda: _GraphStubOk())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Usamos stream para que el SSE no requiera body completo.
        async with c.stream(
            "POST", "/chat", json={"message": "hola", "thread_id": "t-rob-2"}
        ) as resp:
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            # Leemos al menos un evento para confirmar que el stream arrancó.
            lines = []
            async for line in resp.aiter_lines():
                lines.append(line)
                if "[DONE]" in line or len(lines) > 20:
                    break
    # Hubo algo en el stream → no fue 503 ni crash silencioso.
    assert any(line.strip() for line in lines)
