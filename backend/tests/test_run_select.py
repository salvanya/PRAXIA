import pytest

from app import db


@pytest.mark.integration
async def test_run_select_returns_rows_and_columns() -> None:
    rows, columns = await db.run_select("SELECT 1 AS uno, 2 AS dos", timeout_ms=5000, row_limit=200)
    assert rows == [{"uno": 1, "dos": 2}]
    assert columns == ["uno", "dos"]


@pytest.mark.integration
async def test_run_select_blocks_writes() -> None:
    with pytest.raises(Exception):  # noqa: B017 - asyncpg ReadOnlySqlTransactionError
        await db.run_select(
            "INSERT INTO clients (practice_id, full_name) "
            "VALUES ('00000000-0000-0000-0000-000000000001', 'x')",
            timeout_ms=5000,
            row_limit=200,
        )


@pytest.mark.integration
async def test_run_select_respects_row_limit() -> None:
    rows, _ = await db.run_select(
        "SELECT * FROM generate_series(1, 50) AS g(n)", timeout_ms=5000, row_limit=10
    )
    assert len(rows) == 10
