from app.memory import reflect
from app.memory.long_term import Neighbor, Probe
from app.memory.reflect import ExtractedMemories, GateVerdict, MemoryCandidate, NeighborVerdict


class _FakeStructured:
    def __init__(self, value):
        self._value = value

    async def ainvoke(self, messages):
        return self._value


class _FakeLLM:
    def __init__(self, value):
        self._value = value

    def with_structured_output(self, model):
        return _FakeStructured(self._value)


async def test_gate_false_skips_store(monkeypatch) -> None:
    calls = {"store": 0}

    async def _store(*a, **k):
        calls["store"] += 1
        return "id"

    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(
        reflect,
        "_cheap_llm",
        lambda: _FakeLLM(GateVerdict(worth_remembering=False, is_explicit=False, reason="saludo")),
    )
    await reflect.run("p", "hola", "¡Hola!")
    assert calls["store"] == 0


async def test_gate_true_stores_extracted(monkeypatch) -> None:
    stored: list[dict] = []

    async def _probe(practice_id, content):
        return Probe(vector=[0.0] * 1024, related=[])  # sin vecinos → inserta directo

    async def _store(
        practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()
    ):
        stored.append({"content": content, "source": source, "salience": salience})
        return "id"

    llms = iter(
        [
            _FakeLLM(GateVerdict(worth_remembering=True, is_explicit=True, reason="explícito")),
            _FakeLLM(
                ExtractedMemories(
                    memories=[MemoryCandidate(kind="hecho", content="Turnos de 30 min.")]
                )
            ),
        ]
    )
    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(reflect, "_cheap_llm", lambda: next(llms))
    await reflect.run("p", "acordate que los turnos duran 30 min", "Dale.")
    assert stored == [{"content": "Turnos de 30 min.", "source": "explicito", "salience": 0.8}]


async def test_run_is_best_effort(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("ollama down")

    monkeypatch.setattr(reflect, "_cheap_llm", _boom)
    await reflect.run("p", "algo", "respuesta")  # no debe levantar


async def test_run_noop_on_empty_texts(monkeypatch) -> None:
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: (_ for _ in ()).throw(AssertionError("no llamar"))
    )
    await reflect.run("p", "", "")  # texto vacío → no llama al LLM


def _cand(content: str) -> MemoryCandidate:
    return MemoryCandidate(kind="hecho", content=content)


async def test_store_candidate_supersede(monkeypatch) -> None:
    seen: dict = {}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[Neighbor("old1", "Turnos de 30 min.", 0.82)])

    async def _store(
        practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()
    ):
        seen["store"] = {"supersede_ids": list(supersede_ids), "vector": vector, "content": content}
        return "new1"

    async def _touch(ids):
        seen["touch"] = ids

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(reflect.long_term, "touch_last_used", _touch)
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: _FakeLLM(NeighborVerdict(relation="supersede", reason="x"))
    )
    await reflect._store_candidate("p", _cand("Turnos de 45 min."), "reflexion", 0.5)
    assert seen["store"]["supersede_ids"] == ["old1"]
    assert seen["store"]["vector"] == [0.1] * 1024
    assert "touch" not in seen


async def test_store_candidate_duplicate_touches_not_stores(monkeypatch) -> None:
    seen: dict = {"store": False}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[Neighbor("old1", "Turnos de 30 min.", 0.99)])

    async def _store(*a, **k):
        seen["store"] = True

    async def _touch(ids):
        seen["touch"] = ids

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(reflect.long_term, "touch_last_used", _touch)
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: _FakeLLM(NeighborVerdict(relation="duplicate", reason="x"))
    )
    await reflect._store_candidate("p", _cand("Los turnos duran 30 minutos."), "reflexion", 0.5)
    assert seen["touch"] == ["old1"] and seen["store"] is False


async def test_store_candidate_distinct_inserts_without_supersede(monkeypatch) -> None:
    seen: dict = {}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[Neighbor("old1", "Atendemos sábados.", 0.7)])

    async def _store(
        practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()
    ):
        seen["supersede_ids"] = list(supersede_ids)

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(
        reflect, "_cheap_llm", lambda: _FakeLLM(NeighborVerdict(relation="distinct", reason="x"))
    )
    await reflect._store_candidate("p", _cand("Los turnos duran 30 min."), "reflexion", 0.5)
    assert seen["supersede_ids"] == []


async def test_store_candidate_no_related_skips_judge(monkeypatch) -> None:
    seen: dict = {}

    async def _probe(practice_id, content):
        return Probe(vector=[0.1] * 1024, related=[])

    async def _store(
        practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()
    ):
        seen["supersede_ids"] = list(supersede_ids)

    monkeypatch.setattr(reflect.long_term, "probe", _probe)
    monkeypatch.setattr(reflect.long_term, "store", _store)
    monkeypatch.setattr(
        reflect,
        "_cheap_llm",
        lambda: (_ for _ in ()).throw(AssertionError("no juzgar sin vecinos")),
    )
    await reflect._store_candidate("p", _cand("Nuevo hecho."), "reflexion", 0.5)
    assert seen["supersede_ids"] == []


async def test_store_candidate_disabled_uses_legacy_store(monkeypatch) -> None:
    from app.config import Settings

    seen: dict = {}

    async def _store(
        practice_id, *, kind, content, source, salience, vector=None, supersede_ids=()
    ):
        seen["vector"] = vector

    monkeypatch.setattr(
        reflect, "get_settings", lambda: Settings(memory_contradiction_enabled=False)
    )
    monkeypatch.setattr(
        reflect.long_term,
        "probe",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no probe")),
    )
    monkeypatch.setattr(reflect.long_term, "store", _store)
    await reflect._store_candidate("p", _cand("hecho"), "reflexion", 0.5)
    assert seen["vector"] is None  # camino legacy (sin vector → dedup)


async def test_judge_neighbor_none_is_distinct(monkeypatch) -> None:
    monkeypatch.setattr(reflect, "_cheap_llm", lambda: _FakeLLM(None))
    assert await reflect.judge_neighbor("a", "b") == "distinct"
