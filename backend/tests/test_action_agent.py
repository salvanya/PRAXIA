from datetime import UTC, datetime

from app import db
from app.agents import action_agent
from app.agents.action_agent import (
    ProposalResult,
    ProposedAppointment,
    clarify_or_abstain_appointment,
    clarify_or_abstain_client,
)
from app.agents.resolvers import AppointmentResolution, ClientResolution

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedAppointment) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedAppointment) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _patch_db(monkeypatch, *, clients, pracs_by_name=None, active_pracs=None):  # type: ignore[no-untyped-def]
    async def _clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    async def _pracs_by_name(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return pracs_by_name or []

    async def _active(practice_id):  # type: ignore[no-untyped-def]
        return active_pracs or []

    monkeypatch.setattr(db, "find_clients_by_name", _clients)
    monkeypatch.setattr(db, "find_practitioners_by_name", _pracs_by_name)
    monkeypatch.setattr(db, "list_active_practitioners", _active)


async def test_happy_path_defaults_single_practitioner(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment(
        "agendá a Ana mañana 10", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    assert result.proposed_action is not None
    params = result.proposed_action["params"]
    assert params["client_id"] == "c1"
    assert params["practitioner_id"] == "p1"
    assert params["start_at"] == "2026-06-30T10:00:00+00:00"
    assert params["end_at"] == "2026-06-30T10:30:00+00:00"
    assert "Ana López" in result.proposed_action["summary"]


async def test_abstains_when_client_not_found(monkeypatch) -> None:
    _patch_db(monkeypatch, clients=[], active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}])
    llm = FakeGenLLM(ProposedAppointment(client_name="Zzz", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Zzz", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "client_not_found"
    assert "Zzz" in result.message


async def test_abstains_when_client_ambiguous(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}, {"id": "c2", "full_name": "Ana Pérez"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "client_ambiguous"


async def test_abstains_when_practitioner_unspecified_and_many(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[
            {"id": "p1", "full_name": "Dra. Gómez"},
            {"id": "p2", "full_name": "Dr. Ruiz"},
        ],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "practitioner_unspecified"


async def test_abstains_on_bad_datetime(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="no es fecha"))
    result = await action_agent.propose_appointment("agendá a Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "datetime_parse_failed"


def test_proposal_result_is_a_dataclass() -> None:
    r = ProposalResult(proposed_action=None, abstained=True, message="m", reason="r")
    assert r.abstained and r.message == "m"


async def test_abstains_when_named_practitioner_not_found(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        pracs_by_name=[],
    )
    llm = FakeGenLLM(
        ProposedAppointment(
            client_name="Ana",
            practitioner_name="Dr. X",
            start_at="2026-06-30T10:00:00+00:00",
        )
    )
    result = await action_agent.propose_appointment(
        "agendá a Ana con Dr. X", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained
    assert result.reason == "practitioner_not_found"


async def test_abstains_when_named_practitioner_ambiguous(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        pracs_by_name=[{"id": "p1", "full_name": "Dr. A"}, {"id": "p2", "full_name": "Dr. B"}],
    )
    llm = FakeGenLLM(
        ProposedAppointment(
            client_name="Ana",
            practitioner_name="Dr",
            start_at="2026-06-30T10:00:00+00:00",
        )
    )
    result = await action_agent.propose_appointment(
        "agendá a Ana con Dr", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained
    assert result.reason == "practitioner_ambiguous"


async def test_happy_path_named_practitioner(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        pracs_by_name=[{"id": "p9", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(
        ProposedAppointment(
            client_name="Ana",
            practitioner_name="Gómez",
            start_at="2026-06-30T10:00:00+00:00",
        )
    )
    result = await action_agent.propose_appointment(
        "agendá a Ana con Gómez", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["practitioner_id"] == "p9"


async def test_abstains_when_no_practitioners(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá a Ana", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "no_practitioners"


async def test_abstains_when_extract_fails(monkeypatch) -> None:
    class _RaisingStructured:
        async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class FakeRaisingLLM:
        def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
            return _RaisingStructured()

    result = await action_agent.propose_appointment(
        "agendá", "pid", now=NOW, gen_llm=FakeRaisingLLM()
    )
    assert result.abstained
    assert result.reason == "extract_failed"


async def test_abstains_when_client_name_empty(monkeypatch) -> None:
    _patch_db(
        monkeypatch,
        clients=[{"id": "c1", "full_name": "Ana López"}],
        active_pracs=[{"id": "p1", "full_name": "Dra. Gómez"}],
    )
    llm = FakeGenLLM(ProposedAppointment(client_name="  ", start_at="2026-06-30T10:00:00+00:00"))
    result = await action_agent.propose_appointment("agendá el turno", "pid", now=NOW, gen_llm=llm)
    assert result.abstained
    assert result.reason == "client_missing"


def test_clarify_or_abstain_client_ambiguous_sets_clarification() -> None:
    res = ClientResolution(
        None, "Hay varios", "client_ambiguous", candidates=[{"id": "1"}, {"id": "2"}]
    )
    pr = clarify_or_abstain_client(res)
    assert pr.abstained and pr.clarification is not None
    assert pr.clarification.stage == "client" and len(pr.clarification.candidates) == 2


def test_clarify_or_abstain_client_not_found_has_no_clarification() -> None:
    res = ClientResolution(None, "No encontré", "client_not_found")
    pr = clarify_or_abstain_client(res)
    assert pr.abstained and pr.clarification is None


def test_clarify_or_abstain_appointment_ambiguous_sets_stage() -> None:
    res = AppointmentResolution(
        None, "Varios turnos", "appointment_ambiguous", candidates=[{"id": "a1"}]
    )
    pr = clarify_or_abstain_appointment(res)
    assert pr.clarification is not None and pr.clarification.stage == "appointment"


async def test_create_client_override_skips_resolution(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    async def _list_pracs(practice_id):  # type: ignore[no-untyped-def]
        return [{"id": "p1", "full_name": "Dra. Gómez"}]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    monkeypatch.setattr(db, "list_active_practitioners", _list_pracs)
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-07-05T10:00:00"))
    result = await action_agent.propose_appointment(
        "agendá un turno para Ana el 5/7 10:00",
        "pid",
        now=NOW,
        gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"},
    )
    assert not called["clients"] and result.proposed_action is not None
    assert result.proposed_action["params"]["client_id"] == "c1"


async def test_create_client_ambiguous_clarification(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedAppointment(client_name="Ana", start_at="2026-07-05T10:00:00"))
    result = await action_agent.propose_appointment(
        "agendá un turno para Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.clarification is not None and result.clarification.stage == "client"


async def test_create_client_not_found_still_abstains(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedAppointment(client_name="Zzz", start_at="2026-07-05T10:00:00"))
    result = await action_agent.propose_appointment(
        "agendá un turno para Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found" and result.clarification is None
