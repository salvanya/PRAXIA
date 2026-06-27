from app.agents import sql_present


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        return _Msg(self._content)


async def test_empty_rows_returns_fixed_message() -> None:
    out = await sql_present.synthesize_sql_answer("¿cuántos?", [], [])
    assert out == sql_present.SQL_EMPTY_MESSAGE


async def test_scalar_answer_keeps_verbatim_number() -> None:
    out = await sql_present.synthesize_sql_answer(
        "¿cuántos turnos?",
        [{"total": 12}],
        ["total"],
        llm=FakeLLM("Tenés 12 turnos esta semana."),
    )
    assert out == "Tenés 12 turnos esta semana."


async def test_guard_falls_back_when_number_hallucinated() -> None:
    out = await sql_present.synthesize_sql_answer(
        "¿cuántos turnos?",
        [{"total": 12}],
        ["total"],
        llm=FakeLLM("Tenés 99 turnos."),  # 99 no está en las filas
    )
    assert out == "Resultado: 12"


def test_render_rows_markdown_builds_table() -> None:
    md = sql_present.render_rows_markdown(
        [{"full_name": "Ana"}, {"full_name": "Beto"}], ["full_name"]
    )
    assert "| full_name |" in md
    assert "| Ana |" in md and "| Beto |" in md
