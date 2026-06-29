from datetime import UTC, datetime

from app import db
from app.agents import reschedule_agent
from app.agents.reschedule_agent import ProposedReschedule

NOW = datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedReschedule) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedReschedule) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _appt(aid="a1", start=datetime(2026, 7, 1, 10, 0, tzinfo=UTC), dur_min=30):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return {
        "id": aid,
        "start_at": start,
        "end_at": start + timedelta(minutes=dur_min),
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


async def test_happy_builds_action_and_preserves_duration(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt(dur_min=45)])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2026-07-03T15:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá el turno de Ana para el jueves 15", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None and pa["kind"] == "reschedule_appointment"
    assert pa["params"]["appointment_id"] == "a1"
    # duración preservada: 45 min
    s = datetime.fromisoformat(pa["params"]["new_start_at"])
    e = datetime.fromisoformat(pa["params"]["new_end_at"])
    assert (e - s).total_seconds() == 45 * 60
    assert "→" in pa["summary"] and "Ana López" in pa["summary"]


async def test_abstains_extract_fail() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await reschedule_agent.propose_reschedule("reprogramá", "pid", now=NOW, gen_llm=_LLM())
    assert result.abstained and result.reason == "extract_failed"


async def test_abstains_new_time_unparseable(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="no-es-fecha"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "datetime_parse_failed"


async def test_abstains_new_time_in_past(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2020-01-01T10:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "new_time_past"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch(monkeypatch, [], [])
    llm = FakeGenLLM(ProposedReschedule(client_name="Zzz", new_start_at="2026-07-03T15:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found"


async def test_abstains_appointment_none(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [])
    llm = FakeGenLLM(ProposedReschedule(client_name="Ana", new_start_at="2026-07-03T15:00:00"))
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "appointment_none"


async def test_unparseable_current_when_degrades(monkeypatch) -> None:
    _patch(monkeypatch, [{"id": "c1", "full_name": "Ana López"}], [_appt()])
    llm = FakeGenLLM(
        ProposedReschedule(
            client_name="Ana", current_when="no-es-fecha", new_start_at="2026-07-03T15:00:00"
        )
    )
    result = await reschedule_agent.propose_reschedule(
        "reprogramá a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained  # current_when ilegible → None → resolver usa el único candidato
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["appointment_id"] == "a1"
