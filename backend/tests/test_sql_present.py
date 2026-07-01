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


def test_render_rows_markdown_renders_null_as_empty() -> None:
    # NULL de SQL llega como None: debe verse como celda vacía, no el literal "None".
    md = sql_present.render_rows_markdown(
        [{"full_name": "Ana", "email": None}], ["full_name", "email"]
    )
    assert "None" not in md
    assert "| full_name | email |" in md
    assert "| Ana |  |" in md


def test_deterministic_tabular_returns_sentence_not_markdown() -> None:
    out = sql_present._deterministic([{"full_name": "Ana"}, {"full_name": "Beto"}], ["full_name"])
    assert out == "Encontré 2 resultado(s)."
    assert "|" not in out


def test_deterministic_scalar_keeps_resultado_prefix() -> None:
    out = sql_present._deterministic([{"total": 12}], ["total"])
    assert out == "Resultado: 12"


async def test_synth_falls_back_when_llm_emits_markdown_table() -> None:
    # Aunque el prompt lo prohíbe, si el LLM devuelve una tabla markdown, se descarta
    # (la tabla va como artefacto estructurado, no en la prosa).
    out = await sql_present.synthesize_sql_answer(
        "listá los clientes",
        [{"full_name": "Ana"}, {"full_name": "Beto"}],
        ["full_name"],
        llm=FakeLLM("| full_name |\n| --- |\n| Ana |\n| Beto |"),
    )
    assert out == "Encontré 2 resultado(s)."
    assert "|" not in out
