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
