from app.config import Settings
from app.memory import summarize


class _Msg:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, replies):
        self._replies = list(replies)
        self.seen = None

    async def ainvoke(self, messages):
        self.seen = messages
        return _Msg(self._replies.pop(0))


async def test_run_folds_old_and_new_into_prompt():
    fake = _FakeLLM(["Ana es nutricionista."])
    out = await summarize.run(
        "Resumen viejo.", [("human", "Me llamo Ana"), ("ai", "Hola")], llm=fake
    )
    assert out == "Ana es nutricionista."
    prompt = " ".join(t for _, t in fake.seen)
    assert "Resumen viejo." in prompt and "Me llamo Ana" in prompt


async def test_run_empty_new_messages_returns_none():
    assert await summarize.run("prev", [], llm=_FakeLLM(["x"])) is None


async def test_run_retries_on_empty_then_succeeds():
    fake = _FakeLLM(["", "RES"])
    out = await summarize.run("", [("human", "hola")], llm=fake)
    assert out == "RES"


async def test_run_returns_none_when_all_empty():
    fake = _FakeLLM(["", ""])
    assert await summarize.run("", [("human", "hola")], llm=fake) is None


async def test_run_caps_to_max_words(monkeypatch):
    monkeypatch.setattr(summarize, "get_settings", lambda: Settings(summary_max_words=3))
    fake = _FakeLLM(["uno dos tres cuatro cinco"])
    out = await summarize.run("", [("human", "x")], llm=fake)
    assert out.split() == ["uno", "dos", "tres…"] or out == "uno dos tres…"
    assert out.endswith("…")
