from app.agents import sql_agent
from app.agents.sql_agent import SqlDraft, SqlIntentVerdict
from app.semantic_layer.resolver import SemanticLayer

PID = "00000000-0000-0000-0000-000000000001"
GOOD_SQL = f"SELECT count(*) AS total FROM appointments WHERE practice_id = '{PID}'"
LAYER = SemanticLayer(
    schema_context="appointments(practice_id, start_at, status)",
    semantic_context="Métricas: turnos_totales",
    allowed_tables=frozenset({"appointments", "clients", "practitioners"}),
    allowed_columns={"appointments": frozenset({"practice_id", "start_at", "status"})},
)


class _FakeStructured:
    def __init__(self, results: list) -> None:
        self._results = results
        self._i = 0

    async def ainvoke(self, _messages):  # type: ignore[no-untyped-def]
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r


class FakeLLM:
    def __init__(self, *results) -> None:  # type: ignore[no-untyped-def]
        self._results = list(results)

    def with_structured_output(self, _schema):  # type: ignore[no-untyped-def]
        return _FakeStructured(self._results)


def _patch_common(monkeypatch, rows=None):  # type: ignore[no-untyped-def]
    async def _fake_loader(pool=None):  # type: ignore[no-untyped-def]
        return LAYER

    async def _fake_run_select(sql, *, timeout_ms, row_limit):  # type: ignore[no-untyped-def]
        return (rows if rows is not None else [{"total": 12}]), ["total"]

    monkeypatch.setattr(sql_agent, "load_semantic_layer", _fake_loader)
    monkeypatch.setattr(sql_agent, "run_select", _fake_run_select)


async def test_happy_path_returns_rows(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "¿cuántos turnos?",
        PID,
        gen_llm=FakeLLM(SqlDraft(sql=GOOD_SQL)),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=True, reason="ok")),
    )
    assert not result.abstained
    assert result.rows == [{"total": 12}]
    assert result.sql and "appointments" in result.sql


async def test_retries_after_invalid_sql(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "¿cuántos turnos?",
        PID,
        gen_llm=FakeLLM(SqlDraft(sql="INSERT INTO clients DEFAULT VALUES"), SqlDraft(sql=GOOD_SQL)),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=True, reason="ok")),
    )
    assert not result.abstained
    assert result.rows == [{"total": 12}]


async def test_abstains_after_cap(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "algo",
        PID,
        gen_llm=FakeLLM(SqlDraft(sql="INSERT INTO clients DEFAULT VALUES")),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=True, reason="ok")),
    )
    assert result.abstained
    assert result.sql is None


async def test_abstains_when_judge_rejects(monkeypatch) -> None:
    _patch_common(monkeypatch)
    result = await sql_agent.answer_structured(
        "algo",
        PID,
        gen_llm=FakeLLM(SqlDraft(sql=GOOD_SQL)),
        judge_llm=FakeLLM(SqlIntentVerdict(matches=False, reason="no responde")),
    )
    assert result.abstained
