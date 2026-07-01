from datetime import UTC, datetime

import pytest

from app import db
from app.agents import interaction_agent
from app.agents.interaction_agent import ProposedInteraction
from app.guardrails import pii

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


async def test_interaction_client_override_skips_resolution(monkeypatch) -> None:
    called = {"clients": False}

    async def _find_clients(*a, **k):  # type: ignore[no-untyped-def]
        called["clients"] = True
        return []

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", type="nota", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá una nota de Ana",
        "pid",
        now=NOW,
        gen_llm=llm,
        client_override={"id": "c1", "full_name": "Ana López"},
    )
    assert not called["clients"] and result.proposed_action is not None


async def test_interaction_client_ambiguous_clarification(monkeypatch) -> None:
    async def _find_clients(practice_id, name, *, limit):  # type: ignore[no-untyped-def]
        return [{"id": "1", "full_name": "Ana A"}, {"id": "2", "full_name": "Ana B"}]

    monkeypatch.setattr(db, "find_clients_by_name", _find_clients)
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", type="nota", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá una nota de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.clarification is not None and result.clarification.stage == "client"
    assert result.clarification.candidates[0]["id"] == "1"


# ---------------------------------------------------------------------------
# Autouse fixture: por defecto la redacción es identidad, para que los tests
# existentes (que asertan summary/content crudos) sigan verdes.
# Los tests de redacción la sobreescriben con su propio monkeypatch.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _identity_redact(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(interaction_agent.pii, "redact", lambda t: (t, {}))


async def test_redacts_summary_and_content(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])

    def _fake_redact(text: str) -> tuple[str, dict]:
        red = text.replace("Ana", "<NOMBRE>").replace("12.345.678", "<DNI>")
        return red, {}

    monkeypatch.setattr(interaction_agent.pii, "redact", _fake_redact)
    llm = FakeGenLLM(
        ProposedInteraction(
            client_name="Ana",
            type="nota",
            summary="Nota sobre Ana",
            content="Ana pasó el DNI 12.345.678",
        )
    )
    result = await interaction_agent.propose_interaction(
        "agregá una nota sobre Ana", "pid", now=NOW, gen_llm=llm
    )
    pa = result.proposed_action
    assert pa is not None
    assert pa["params"]["content"] == "<NOMBRE> pasó el DNI <DNI>"
    assert pa["params"]["summary"] == "Nota sobre <NOMBRE>"
    assert pa["params"]["client_name"] == "Ana López"  # intacto (del resolver)
    assert "<NOMBRE>" in pa["summary"]  # la tarjeta muestra el resumen redactado


async def test_fail_closed_when_pii_unavailable(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])

    def _boom(text: str) -> tuple[str, dict]:
        raise pii.PiiUnavailable("no model")

    monkeypatch.setattr(interaction_agent.pii, "redact", _boom)
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá algo de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "pii_unavailable"
    assert result.proposed_action is None
