from app.agents.sql_agent import validate_select

PID = "00000000-0000-0000-0000-000000000001"
ALLOWED = frozenset({"appointments", "clients", "practitioners"})


def _v(sql: str):
    return validate_select(sql, ALLOWED, PID, row_limit=200)


def test_accepts_select_with_practice_filter() -> None:
    r = _v(f"SELECT count(*) FROM appointments WHERE practice_id = '{PID}'")
    assert r.ok
    assert "LIMIT" in r.sql.upper()


def test_rejects_insert() -> None:
    assert not _v(f"INSERT INTO clients (practice_id) VALUES ('{PID}')").ok


def test_rejects_update() -> None:
    assert not _v(f"UPDATE clients SET status='baja' WHERE practice_id='{PID}'").ok


def test_rejects_delete() -> None:
    assert not _v(f"DELETE FROM clients WHERE practice_id='{PID}'").ok


def test_rejects_multiple_statements() -> None:
    assert not _v(f"SELECT 1 FROM clients WHERE practice_id='{PID}'; DROP TABLE clients").ok


def test_rejects_table_outside_allowlist() -> None:
    assert not _v(f"SELECT * FROM invoices WHERE practice_id = '{PID}'").ok


def test_rejects_missing_practice_filter() -> None:
    assert not _v("SELECT count(*) FROM appointments").ok


def test_injects_limit_when_missing() -> None:
    r = _v(f"SELECT full_name FROM clients WHERE practice_id = '{PID}'")
    assert r.ok and "LIMIT 200" in r.sql.upper().replace("  ", " ")


def test_clamps_limit_over_cap() -> None:
    r = _v(f"SELECT full_name FROM clients WHERE practice_id = '{PID}' LIMIT 9999")
    assert r.ok and "9999" not in r.sql


def test_rejects_practice_filter_under_or() -> None:
    assert not _v(f"SELECT * FROM clients WHERE practice_id = '{PID}' OR 1=1").ok


def test_rejects_practice_filter_in_projection_without_where() -> None:
    assert not _v(f"SELECT full_name, practice_id = '{PID}' AS mine FROM clients").ok


def test_rejects_practice_filter_only_in_join_on() -> None:
    assert not _v(f"SELECT a.id FROM appointments a JOIN clients c ON c.practice_id = '{PID}'").ok


def test_accepts_practice_filter_as_and_conjunct() -> None:
    r = _v(f"SELECT count(*) FROM appointments WHERE start_at >= now() AND practice_id = '{PID}'")
    assert r.ok


def test_accepts_join_with_outer_practice_filter() -> None:
    r = _v(
        "SELECT p.full_name FROM appointments a "
        "JOIN practitioners p ON a.practitioner_id = p.id "
        f"WHERE a.practice_id = '{PID}'"
    )
    assert r.ok


def test_rejects_select_into() -> None:
    assert not _v(f"SELECT full_name INTO appointments FROM clients WHERE practice_id = '{PID}'").ok
