import pytest
from httpx import ASGITransport, AsyncClient

from app import db
from app.graph import nodes
from app.main import app
from tests.test_e2e_llm import _parse_sse


@pytest.mark.llm
@pytest.mark.integration
async def test_real_llm_counts_appointments_this_week() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pool = await db.get_pool()
    expected = await pool.fetchval(
        "SELECT count(*) FROM appointments "
        "WHERE start_at >= date_trunc('week', now()) "
        "AND start_at < date_trunc('week', now()) + interval '7 days'"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with c.stream(
            "POST", "/chat", json={"message": "¿cuántos turnos hay esta semana?"}
        ) as resp:
            body = "".join([line + "\n" async for line in resp.aiter_lines()])
    answer, sources = _parse_sse(body)
    assert str(expected) in answer
    assert sources == []


@pytest.mark.llm
@pytest.mark.integration
async def test_real_llm_abstains_on_untranslatable_question() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with c.stream(
            "POST", "/chat", json={"message": "sumá todos los números primos del universo"}
        ) as resp:
            body = "".join([line + "\n" async for line in resp.aiter_lines()])
    answer, _ = _parse_sse(body)
    # router puede mandarlo a sql o a out_of_scope; en ambos casos no inventa datos
    assert answer.strip() != ""
    assert nodes.SQL_ABSTAIN_MESSAGE in answer or nodes.SCOPE_MESSAGE in answer
