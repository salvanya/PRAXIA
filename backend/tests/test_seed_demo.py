import pytest

from app import db


@pytest.mark.integration
async def test_seed_demo_creates_data_with_appointments_this_week() -> None:
    from seed_demo import seed_demo

    counts = await seed_demo()
    assert counts["practitioners"] >= 3
    assert counts["clients"] >= 30
    assert counts["appointments"] >= 12

    pool = await db.get_pool()
    n_week = await pool.fetchval(
        "SELECT count(*) FROM appointments "
        "WHERE start_at >= date_trunc('week', now()) "
        "AND start_at < date_trunc('week', now()) + interval '7 days'"
    )
    assert n_week >= 12

    n_active = await pool.fetchval("SELECT count(*) FROM clients WHERE status = 'activo'")
    assert n_active >= 1

    n_email = await pool.fetchval("SELECT count(*) FROM clients WHERE email IS NOT NULL")
    assert n_email >= 30
