from datetime import UTC, datetime

from langchain_core.messages import AIMessage

from app.agents import write_tools
from app.agents.write_tools import REGISTRY, classify_write_action


class FakeSeqLLM:
    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.calls = 0

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        c = self._contents[min(len(self._contents) - 1, self.calls)]
        self.calls += 1
        return AIMessage(content=c)


async def test_classify_returns_kind() -> None:
    assert (
        await classify_write_action("registrá que llamé a Ana", llm=FakeSeqLLM("log_interaction"))
        == "log_interaction"
    )
    assert (
        await classify_write_action("agendá un turno", llm=FakeSeqLLM("create_appointment"))
        == "create_appointment"
    )
    assert (
        await classify_write_action("reprogramá el turno", llm=FakeSeqLLM("unsupported"))
        == "unsupported"
    )


async def test_classify_substring_retry_and_fallback() -> None:
    # substring: el modelo envuelve la opción en una frase
    assert (
        await classify_write_action("x", llm=FakeSeqLLM("la acción es log_interaction"))
        == "log_interaction"
    )
    # retry: respuesta no clara en el 1er intento, válida en el 2do
    seq = FakeSeqLLM("mmm no sé", "create_appointment")
    assert await classify_write_action("x", llm=seq) == "create_appointment"
    assert seq.calls == 2
    # fallback fail-closed: nunca devuelve una opción reconocible
    assert await classify_write_action("x", llm=FakeSeqLLM("???")) == "unsupported"


def test_registry_has_all_tools() -> None:
    assert set(REGISTRY) == {"create_appointment", "log_interaction", "cancel_appointment"}
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


async def test_write_appointment_adapter_drops_display_keys(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_create(practice_id, client_id, practitioner_id, start_at, end_at, **kw):  # type: ignore[no-untyped-def]
        captured.update(
            practice_id=practice_id,
            client_id=client_id,
            practitioner_id=practitioner_id,
            start_at=start_at,
            end_at=end_at,
            **kw,
        )
        return {"id": "a1", "status": "programado"}

    monkeypatch.setattr(write_tools.db, "create_appointment", _fake_create)
    params = {
        "client_id": "c1",
        "client_name": "Ana López",
        "practitioner_id": "p1",
        "practitioner_name": "Dra. Gómez",
        "start_at": "2026-06-30T10:00:00+00:00",
        "end_at": "2026-06-30T10:30:00+00:00",
        "reason": "control",
        "channel": "presencial",
        "status": "programado",
    }
    row = await write_tools._write_appointment("pid", params)
    assert captured["client_id"] == "c1" and captured["practitioner_id"] == "p1"
    assert captured["start_at"] == datetime(2026, 6, 30, 10, 0, tzinfo=UTC)  # ISO→datetime
    assert "client_name" not in captured and "practitioner_name" not in captured
    assert row["id"] == "a1"


async def test_classify_routes_cancel() -> None:
    assert (
        await classify_write_action("cancelá el turno de Ana", llm=FakeSeqLLM("cancel_appointment"))
        == "cancel_appointment"
    )


async def test_write_cancel_adapter_wraps_row(monkeypatch) -> None:
    async def _fake_cancel(practice_id, appointment_id):  # type: ignore[no-untyped-def]
        return {"id": appointment_id, "status": "cancelado", "start_at": None}

    monkeypatch.setattr(write_tools.db, "cancel_appointment", _fake_cancel)
    row = await write_tools._write_cancel("pid", {"appointment_id": "a1", "client_name": "Ana"})
    assert row["cancelled"] is True and row["status"] == "cancelado"


async def test_write_cancel_adapter_handles_none(monkeypatch) -> None:
    async def _fake_cancel(practice_id, appointment_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(write_tools.db, "cancel_appointment", _fake_cancel)
    row = await write_tools._write_cancel("pid", {"appointment_id": "a1"})
    assert row == {"cancelled": False}


def test_cancel_receipt_ok_and_not_ok() -> None:
    params = {
        "client_name": "Ana López",
        "practitioner_name": "Dra. Gómez",
        "start_at": "2026-07-01T10:00:00+00:00",
    }
    ok = write_tools.format_cancel_receipt(params, {"cancelled": True})
    assert "✅" in ok and "Ana López" in ok and "Dra. Gómez" in ok
    bad = write_tools.format_cancel_receipt(params, {"cancelled": False})
    assert "⚠️" in bad
