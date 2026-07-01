import pytest

from app import db


@pytest.mark.integration
async def test_appointments_table_has_expected_columns() -> None:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'appointments'"
    )
    cols = {r["column_name"] for r in rows}
    assert {
        "practice_id",
        "client_id",
        "practitioner_id",
        "start_at",
        "end_at",
        "status",
    } <= cols


@pytest.mark.integration
async def test_interactions_table_has_expected_columns() -> None:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'interactions'"
    )
    cols = {r["column_name"] for r in rows}
    assert {
        "practice_id",
        "client_id",
        "type",
        "summary",
        "content",
        "occurred_at",
        "source",
    } <= cols


@pytest.mark.integration
async def test_documents_table_has_pii_summary() -> None:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'"
    )
    cols = {r["column_name"] for r in rows}
    assert "pii_summary" in cols
