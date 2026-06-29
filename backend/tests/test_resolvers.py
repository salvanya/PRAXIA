from app import db
from app.agents import resolvers


def _patch(monkeypatch, clients):  # type: ignore[no-untyped-def]
    async def _find(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    monkeypatch.setattr(db, "find_clients_by_name", _find)


async def test_resolves_single(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana"}])
    r = await resolvers.resolve_single_client("pid", "Ana", limit=5)
    assert r.client == {"id": "c1", "full_name": "Ana"}
    assert r.abstain_reason == "ok"


async def test_empty_name_is_missing(monkeypatch) -> None:
    r = await resolvers.resolve_single_client("pid", "  ", limit=5)
    assert r.client is None and r.abstain_reason == "client_missing"


async def test_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [])
    r = await resolvers.resolve_single_client("pid", "Zzz", limit=5)
    assert r.client is None and r.abstain_reason == "client_not_found"
    assert "Zzz" in r.abstain_message


async def test_ambiguous(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}])
    r = await resolvers.resolve_single_client("pid", "Ana", limit=5)
    assert r.client is None and r.abstain_reason == "client_ambiguous"


from datetime import UTC, datetime

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)
CLIENT = {"id": "c1", "full_name": "Ana López"}


def _appt(aid, dt, prof="Dra. Gómez", status="programado"):  # type: ignore[no-untyped-def]
    return {
        "id": aid,
        "start_at": dt,
        "end_at": dt,
        "status": status,
        "practitioner_id": "p1",
        "practitioner_full_name": prof,
    }


def _patch_appts(monkeypatch, appts):  # type: ignore[no-untyped-def]
    async def _find(practice_id, client_id, *, now, limit):  # type: ignore[no-untyped-def]
        return appts

    monkeypatch.setattr(db, "find_cancellable_appointments", _find)


async def test_appt_none_abstains(monkeypatch) -> None:
    _patch_appts(monkeypatch, [])
    r = await resolvers.resolve_single_appointment("pid", CLIENT, None, now=NOW, limit=5)
    assert r.appointment is None and r.abstain_reason == "appointment_none"


async def test_appt_single_ok(monkeypatch) -> None:
    a = _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
    _patch_appts(monkeypatch, [a])
    r = await resolvers.resolve_single_appointment("pid", CLIENT, None, now=NOW, limit=5)
    assert r.appointment == a and r.abstain_reason == "ok"


async def test_appt_many_no_hint_ambiguous(monkeypatch) -> None:
    _patch_appts(
        monkeypatch,
        [
            _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC)),
            _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC)),
        ],
    )
    r = await resolvers.resolve_single_appointment("pid", CLIENT, None, now=NOW, limit=5)
    assert r.appointment is None and r.abstain_reason == "appointment_ambiguous"
    assert "Ana López" in r.abstain_message


async def test_appt_hint_filters_to_one(monkeypatch) -> None:
    _patch_appts(
        monkeypatch,
        [
            _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC)),
            _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC)),
        ],
    )
    when = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)  # solo el día
    r = await resolvers.resolve_single_appointment("pid", CLIENT, when, now=NOW, limit=5)
    assert r.appointment is not None and r.appointment["id"] == "a2"


async def test_appt_hint_day_with_no_turno_not_found(monkeypatch) -> None:
    _patch_appts(monkeypatch, [_appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC))])
    when = datetime(2026, 7, 9, 0, 0, tzinfo=UTC)
    r = await resolvers.resolve_single_appointment("pid", CLIENT, when, now=NOW, limit=5)
    assert r.appointment is None and r.abstain_reason == "appointment_not_found"
    assert "01/07" in r.abstain_message  # lista los próximos reales


async def test_appt_hint_time_disambiguates_same_day(monkeypatch) -> None:
    _patch_appts(
        monkeypatch,
        [
            _appt("a1", datetime(2026, 7, 1, 10, 0, tzinfo=UTC)),
            _appt("a2", datetime(2026, 7, 1, 15, 0, tzinfo=UTC)),
        ],
    )
    when = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
    r = await resolvers.resolve_single_appointment("pid", CLIENT, when, now=NOW, limit=5)
    assert r.appointment is not None and r.appointment["id"] == "a2"
