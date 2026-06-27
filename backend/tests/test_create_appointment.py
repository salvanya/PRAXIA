from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import db
from app.config import get_settings


@pytest.mark.integration
async def test_create_appointment_inserts_and_returns_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = await db.find_clients_by_name(pid, "", limit=1)
    pracs = await db.list_active_practitioners(pid)
    assert client and pracs  # el seed cargó datos
    start = datetime.now(UTC) + timedelta(days=1)
    row = await db.create_appointment(
        pid, client[0]["id"], pracs[0]["id"], start, start + timedelta(minutes=30),
        reason="control", channel="presencial",
    )
    assert row["status"] == "programado"
    assert row["id"]


@pytest.mark.integration
async def test_create_appointment_rejects_foreign_ids() -> None:
    pid = get_settings().practice_id
    with pytest.raises(RuntimeError):
        await db.create_appointment(
            pid, str(uuid4()), str(uuid4()),
            datetime.now(UTC), datetime.now(UTC) + timedelta(minutes=30),
        )


@pytest.mark.integration
async def test_find_clients_by_name_is_tenant_scoped() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    rows = await db.find_clients_by_name(pid, "", limit=3)
    assert all("id" in r and "full_name" in r for r in rows)
