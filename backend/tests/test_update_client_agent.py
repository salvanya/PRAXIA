from datetime import UTC, datetime

from app import db
from app.agents import update_client_agent
from app.agents.update_client_agent import ProposedClientUpdate

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedClientUpdate) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedClientUpdate) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _patch(monkeypatch, clients, current):  # type: ignore[no-untyped-def]
    async def _find(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    async def _get(practice_id, client_id):  # type: ignore[no-untyped-def]
        return current

    monkeypatch.setattr(db, "find_clients_by_name", _find)
    monkeypatch.setattr(db, "get_client", _get)


_CLIENT = [{"id": "c1", "full_name": "Ana López"}]
_CURRENT = {"id": "c1", "full_name": "Ana López", "phone": "11-1111-1111", "email": None, "status": "activo", "dob": None}


async def test_happy_single_field(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", phone="11-2233-4455"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Ana a 11-2233-4455", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None and pa["kind"] == "update_client"
    assert pa["params"]["client_id"] == "c1" and pa["params"]["phone"] == "11-2233-4455"
    assert "email" not in pa["params"]  # solo el campo cambiado
    assert "11-1111-1111" in pa["summary"] and "11-2233-4455" in pa["summary"]  # antes→después


async def test_happy_multi_field(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", email="ana@x.com", status="baja"))
    result = await update_client_agent.propose_update_client(
        "actualizá el email de Ana y dala de baja", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    p = result.proposed_action["params"]
    assert p["email"] == "ana@x.com" and p["status"] == "baja" and "phone" not in p


async def test_abstains_extract_fail() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await update_client_agent.propose_update_client(
        "cambiá", "pid", now=NOW, gen_llm=_LLM()
    )
    assert result.abstained and result.reason == "extract_failed"


async def test_abstains_no_fields(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana"))  # ningún campo
    result = await update_client_agent.propose_update_client(
        "tocá algo de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "no_fields"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [], None)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Zzz", phone="123"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found"


async def test_invalid_dob_dropped_keeps_other(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", phone="999", dob="no-es-fecha"))
    result = await update_client_agent.propose_update_client(
        "cambiá el teléfono de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    p = result.proposed_action["params"]
    assert p["phone"] == "999" and "dob" not in p  # dob ilegible se descarta


async def test_invalid_dob_alone_abstains(monkeypatch) -> None:
    _patch(monkeypatch, _CLIENT, _CURRENT)
    llm = FakeGenLLM(ProposedClientUpdate(client_name="Ana", dob="no-es-fecha"))
    result = await update_client_agent.propose_update_client(
        "cambiá la fecha de nacimiento de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "no_fields"
