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
        await classify_write_action("facturá la sesión", llm=FakeSeqLLM("unsupported"))
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
    assert set(REGISTRY) == {
        "create_appointment",
        "log_interaction",
        "cancel_appointment",
        "reschedule_appointment",
        "update_client",
    }
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


async def test_classify_routes_reschedule_and_update_client() -> None:
    assert (
        await classify_write_action(
            "reprogramá el turno de Ana", llm=FakeSeqLLM("reschedule_appointment")
        )
        == "reschedule_appointment"
    )
    assert (
        await classify_write_action(
            "cambiá el teléfono de Ana", llm=FakeSeqLLM("update_client")
        )
        == "update_client"
    )


async def test_write_reschedule_adapter(monkeypatch) -> None:
    captured: dict = {}

    async def _fake(practice_id, appointment_id, new_start_at, new_end_at):  # type: ignore[no-untyped-def]
        captured.update(appointment_id=appointment_id, new_start_at=new_start_at, new_end_at=new_end_at)
        return {"id": appointment_id, "status": "programado", "start_at": new_start_at, "end_at": new_end_at}

    monkeypatch.setattr(write_tools.db, "reschedule_appointment", _fake)
    params = {
        "appointment_id": "a1",
        "new_start_at": "2026-07-03T15:00:00+00:00",
        "new_end_at": "2026-07-03T15:30:00+00:00",
        "client_name": "Ana López",
    }
    row = await write_tools._write_reschedule("pid", params)
    assert row["rescheduled"] is True
    assert captured["new_start_at"] == datetime(2026, 7, 3, 15, 0, tzinfo=UTC)  # ISO→datetime
    assert "client_name" not in captured


async def test_write_reschedule_adapter_handles_none(monkeypatch) -> None:
    async def _fake(practice_id, appointment_id, new_start_at, new_end_at):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(write_tools.db, "reschedule_appointment", _fake)
    row = await write_tools._write_reschedule(
        "pid",
        {"appointment_id": "a1", "new_start_at": "2026-07-03T15:00:00+00:00", "new_end_at": "2026-07-03T15:30:00+00:00"},
    )
    assert row == {"rescheduled": False}


def test_reschedule_receipt_ok_and_not_ok() -> None:
    params = {
        "client_name": "Ana López",
        "practitioner_name": "Dra. Gómez",
        "new_start_at": "2026-07-03T15:00:00+00:00",
    }
    ok = write_tools.format_reschedule_receipt(params, {"rescheduled": True})
    assert "✅" in ok and "Ana López" in ok and "Dra. Gómez" in ok
    bad = write_tools.format_reschedule_receipt(params, {"rescheduled": False})
    assert "⚠️" in bad


async def test_write_update_client_adapter(monkeypatch) -> None:
    captured: dict = {}

    async def _fake(practice_id, client_id, *, phone, email, status, dob):  # type: ignore[no-untyped-def]
        captured.update(client_id=client_id, phone=phone, email=email, status=status, dob=dob)
        return {"id": client_id, "full_name": "Ana López", "phone": phone, "email": email, "status": status, "dob": None}

    monkeypatch.setattr(write_tools.db, "update_client", _fake)
    params = {"client_id": "c1", "client_name": "Ana López", "phone": "11-2233-4455", "status": "baja"}
    row = await write_tools._write_update_client("pid", params)
    assert row["updated"] is True
    assert captured["phone"] == "11-2233-4455" and captured["status"] == "baja"
    assert captured["email"] is None and captured["dob"] is None  # no provistos → None
    assert "client_name" not in captured


async def test_write_update_client_adapter_handles_none(monkeypatch) -> None:
    async def _fake(practice_id, client_id, *, phone, email, status, dob):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(write_tools.db, "update_client", _fake)
    row = await write_tools._write_update_client("pid", {"client_id": "c1", "phone": "9"})
    assert row == {"updated": False}


def test_update_client_receipt_lists_changed_fields() -> None:
    params = {"client_id": "c1", "phone": "11-2233-4455", "status": "baja"}
    ok = write_tools.format_update_client_receipt(params, {"updated": True, "full_name": "Ana López"})
    assert "✅" in ok and "Ana López" in ok
    assert "teléfono" in ok and "11-2233-4455" in ok and "estado" in ok
    assert "email" not in ok  # no cambió → no se lista
    bad = write_tools.format_update_client_receipt(params, {"updated": False})
    assert "⚠️" in bad
