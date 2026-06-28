from datetime import UTC, datetime

from app import db
from app.agents import interaction_agent
from app.agents.interaction_agent import ProposedInteraction

NOW = datetime(2026, 6, 28, 14, 30, tzinfo=UTC)


class _FakeStructured:
    def __init__(self, value: ProposedInteraction) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeGenLLM:
    def __init__(self, value: ProposedInteraction) -> None:
        self._value = value

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._value)


def _patch_clients(monkeypatch, clients):  # type: ignore[no-untyped-def]
    async def _find(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return clients

    monkeypatch.setattr(db, "find_clients_by_name", _find)


async def test_happy_path_builds_action(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])
    llm = FakeGenLLM(
        ProposedInteraction(
            client_name="Ana",
            type="llamada",
            summary="Ana confirmó el turno",
            content="Llamé a Ana y confirmó el turno del martes.",
        )
    )
    result = await interaction_agent.propose_interaction(
        "registrá que llamé a Ana", "pid", now=NOW, gen_llm=llm
    )
    assert not result.abstained
    pa = result.proposed_action
    assert pa is not None
    assert pa["kind"] == "log_interaction"
    p = pa["params"]
    assert p["client_id"] == "c1"
    assert p["type"] == "llamada"
    assert p["summary"] == "Ana confirmó el turno"
    assert p["content"].startswith("Llamé a Ana")
    assert p["occurred_at"] == "2026-06-28T14:30:00+00:00"
    assert p["source"] == "agente"
    assert "Ana López" in pa["summary"]
    assert pa["summary"] != p["summary"]  # card text vs DB column


async def test_default_type_is_nota(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "anotá algo de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.proposed_action is not None
    assert result.proposed_action["params"]["type"] == "nota"


async def test_abstains_client_not_found(monkeypatch) -> None:
    _patch_clients(monkeypatch, [])
    llm = FakeGenLLM(ProposedInteraction(client_name="Zzz", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá algo de Zzz", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_not_found"
    assert "Zzz" in result.message


async def test_abstains_client_ambiguous(monkeypatch) -> None:
    _patch_clients(
        monkeypatch,
        [{"id": "c1", "full_name": "Ana López"}, {"id": "c2", "full_name": "Ana Pérez"}],
    )
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá algo de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_ambiguous"


async def test_abstains_client_empty(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])
    llm = FakeGenLLM(ProposedInteraction(client_name="   ", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá algo", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "client_missing"


async def test_abstains_when_extract_fails() -> None:
    class _Raising:
        async def ainvoke(self, _m):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    class _LLM:
        def with_structured_output(self, _s):  # type: ignore[no-untyped-def]
            return _Raising()

    result = await interaction_agent.propose_interaction("registrá", "pid", now=NOW, gen_llm=_LLM())
    assert result.abstained and result.reason == "extract_failed"
