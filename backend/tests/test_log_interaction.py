from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app import db
from app.config import get_settings


@pytest.mark.integration
async def test_log_interaction_inserts_and_returns_row() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]
    row = await db.log_interaction(
        pid,
        client["id"],
        type="llamada",
        summary="Confirmó el turno",
        content="Llamé al cliente y confirmó.",
        occurred_at=datetime.now(UTC),
    )
    assert row["type"] == "llamada"
    assert row["id"]


@pytest.mark.integration
async def test_log_interaction_rejects_foreign_client() -> None:
    pid = get_settings().practice_id
    with pytest.raises(RuntimeError):
        await db.log_interaction(
            pid,
            str(uuid4()),
            type="nota",
            summary="x",
            content="y",
            occurred_at=datetime.now(UTC),
        )
