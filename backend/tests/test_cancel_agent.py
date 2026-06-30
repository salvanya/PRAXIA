from datetime import UTC, datetime

from app import db
from app.agents import cancel_agent
from app.agents.cancel_agent import ProposedCancellation

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedCancellation) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedCancellation) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _appt(aid="a1", dt=datetime(2026, 7, 1, 10, 0, tzinfo=UTC)):  # type: ignore[no-untyped-def]
    return {
        "id": aid,
        "start_at": dt,
        "end_at": dt,
        "status": "programado",
        "practitioner_id": "p1",
        "practitioner_full_name": "Dra. Gómez",
    }


def _patch(monkeypatch, clients, appts):  # type: ignore[no-untyped-def]
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    async def _find_appts(practice_id, client_id, *, now, limit):  # type: ignore[no-untyped-def]
        return appts

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "find_cancellable_appointments", _find_appts)


async def test_happy_builds_action(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None and pa["kind"] == "cancel_appointment"
    assert pa["params"]["appointment_id"] == "a1"
    assert "Ana López" in pa["summary"] and "Dra. Gómez" in pa["summary"]


async def test_abstains_extract_fail() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await cancel_agent.propose_cancellation("cancelá", "pid", now=NOW, gen_llm=_LLM())
    assert result.abstained and result.reason == "extract_failed"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [], [])
    llm = FakeGenLLM(ProposedCancellation(client_name="Zzz"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found"


async def test_abstains_appointment_none(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "appointment_none"


async def test_unparseable_when_degrades_to_no_hint(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana", when="no-es-fecha"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained  # when ilegible → None → resolver usa el único candidato
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["appointment_id"] == "a1"


async def test_client_override_skips_client_resolution(monkeypatch) -> None:
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    async def _find_appts(practice_id, client_id, *, now, limit):  # type: ignore[no-untyped-def]
        return [_appt()]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "find_cancellable_appointments", _find_appts)
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana",
        "pid",
        now=NOW,
        gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"},
    )
    assert not called["clients"]
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["appointment_id"] == "a1"


async def test_client_ambiguous_returns_clarification(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}], [])
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.clarification is not None and result.clarification.stage == "client"
    assert len(result.clarification.candidates) == 2


async def test_appointment_ambiguous_returns_clarification(monkeypatch) -> None:
    _patch(
        monkeypatch,
        [{"id": "c1", "full_name": "Ana López"}],
        [_appt("a1"), _appt("a2", datetime(2026, 7, 2, 11, 0, tzinfo=UTC))],
    )
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.clarification is not None and result.clarification.stage == "appointment"
    assert len(result.clarification.candidates) == 2


async def test_appointment_override_skips_appt_resolution(monkeypatch) -> None:
    called = {"appts": False}

    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return [{"id": "c1", "full_name": "Ana López"}]

    async def _find_appts(*a, **k):  # type: ignore[no-untyped-def]
        called["appts"] = True
        return []

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "find_cancellable_appointments", _find_appts)
    llm = FakeGenLLM(ProposedCancellation(client_name="Ana"))
    result = await cancel_agent.propose_cancellation(
        "cancelá el turno de Ana", "pid", now=NOW, gen_llm=llm, appointment_override=_appt("aX")
    )
    assert not called["appts"]
    assert result.proposed_action["params"]["appointment_id"] == "aX"
