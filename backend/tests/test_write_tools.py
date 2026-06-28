from datetime import UTC, datetime

from app.agents import write_tools
from app.agents.write_tools import REGISTRY, WriteActionDecision, classify_write_action


class _FakeStructured:
    def __init__(self, value: WriteActionDecision) -> None:
        self._value = value

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return self._value


class FakeClassifyLLM:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(WriteActionDecision(kind=self._kind))


async def test_classify_returns_kind() -> None:
    assert (
        await classify_write_action(
            "registrá que llamé a Ana", llm=FakeClassifyLLM("log_interaction")
        )
        == "log_interaction"
    )
    assert (
        await classify_write_action("agendá un turno", llm=FakeClassifyLLM("create_appointment"))
        == "create_appointment"
    )
    assert (
        await classify_write_action("cancelá el turno", llm=FakeClassifyLLM("unsupported"))
        == "unsupported"
    )


def test_registry_has_both_tools() -> None:
    assert set(REGISTRY) == {"create_appointment", "log_interaction"}
    for kind, tool in REGISTRY.items():
        assert tool.kind == kind
        assert tool.cancel_message


async def test_write_interaction_adapter_maps_params(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_log(practice_id, client_id, *, type, summary, content, occurred_at, source):  # type: ignore[no-untyped-def]
        captured.update(
            practice_id=practice_id,
            client_id=client_id,
            type=type,
            summary=summary,
            content=content,
            occurred_at=occurred_at,
            source=source,
        )
        return {"id": "i1", "occurred_at": occurred_at, "type": type}

    monkeypatch.setattr(write_tools.db, "log_interaction", _fake_log)
    params = {
        "client_id": "c1",
        "client_name": "Ana López",
        "type": "llamada",
        "summary": "Ana confirmó",
        "content": "Llamé a Ana.",
        "occurred_at": "2026-06-28T14:30:00+00:00",
        "source": "agente",
    }
    row = await write_tools._write_interaction("pid", params)
    assert captured["client_id"] == "c1"
    assert captured["type"] == "llamada"
    assert captured["occurred_at"] == datetime(2026, 6, 28, 14, 30, tzinfo=UTC)  # ISO→datetime
    assert "client_name" not in captured  # display-only key dropped
    assert row["id"] == "i1"


def test_interaction_receipt_is_deterministic() -> None:
    params = {
        "client_name": "Ana López",
        "type": "llamada",
        "occurred_at": "2026-06-28T14:30:00+00:00",
    }
    msg = write_tools.format_interaction_receipt(params, {"id": "i1"})
    assert "✅" in msg and "llamada" in msg and "Ana López" in msg
