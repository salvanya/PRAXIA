import json
from types import SimpleNamespace

from app.main import _sse_event_stream


class _FakeGraph:
    def __init__(self, items):  # type: ignore[no-untyped-def]
        self._items = items

    def astream(self, inp, config, *, stream_mode):  # type: ignore[no-untyped-def]
        assert stream_mode == ["custom", "updates"], stream_mode

        async def gen():  # type: ignore[no-untyped-def]
            for it in self._items:
                yield it

        return gen()


async def test_stream_translates_token_sources_confirm_done() -> None:
    action = {"kind": "create_appointment", "summary": "Crear turno: Ana", "params": {}}
    graph = _FakeGraph(
        [
            ("custom", {"kind": "token", "text": "hola"}),
            ("custom", {"kind": "sources", "sources": []}),
            ("updates", {"propose_appointment": {"proposed_action": action}}),  # ignorado
            ("updates", {"__interrupt__": (SimpleNamespace(value=action),)}),
        ]
    )
    config = {"configurable": {"thread_id": "t1"}}
    events = [e async for e in _sse_event_stream(graph, None, config)]

    assert {"event": "token", "data": "hola"} in events
    assert {"event": "sources", "data": "[]"} in events
    confirm = next(e for e in events if e["event"] == "confirm")
    payload = json.loads(confirm["data"])
    assert payload["thread_id"] == "t1"
    assert payload["action"] == action
    assert events[-1] == {"event": "done", "data": "[DONE]"}


async def test_stream_forwards_table_event_with_json_safe_serialization() -> None:
    from datetime import UTC, datetime

    graph = _FakeGraph(
        [
            (
                "custom",
                {
                    "kind": "table",
                    "columns": ["cliente", "fecha"],
                    "rows": [{"cliente": "Ana", "fecha": datetime(2026, 7, 10, 10, 0, tzinfo=UTC)}],
                    "sql": "SELECT cliente, fecha FROM turnos",
                },
            ),
        ]
    )
    config = {"configurable": {"thread_id": "t1"}}
    events = [e async for e in _sse_event_stream(graph, None, config)]

    table = next(e for e in events if e["event"] == "table")
    payload = json.loads(table["data"])
    assert payload["columns"] == ["cliente", "fecha"]
    assert payload["sql"] == "SELECT cliente, fecha FROM turnos"
    # datetime → string vía default=str (no explota json.dumps)
    assert payload["rows"][0]["cliente"] == "Ana"
    assert isinstance(payload["rows"][0]["fecha"], str)
    assert "2026-07-10" in payload["rows"][0]["fecha"]
