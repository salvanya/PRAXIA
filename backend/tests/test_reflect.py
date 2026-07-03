from app.memory import reflect
from app.memory.reflect import ExtractedMemories, GateVerdict, MemoryCandidate


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

    async def _store(practice_id, *, kind, content, source, salience):
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
