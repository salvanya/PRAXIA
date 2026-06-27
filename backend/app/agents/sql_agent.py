import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from app.config import get_settings
from app.db import run_select
from app.llm import make_llm
from app.semantic_layer.resolver import SemanticLayer, load_semantic_layer

_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
)


@dataclass
class ValidationResult:
    ok: bool
    sql: str
    reason: str


@dataclass
class SqlResult:
    sql: str | None
    rows: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    abstained: bool = False
    reason: str = ""


class SqlIntentVerdict(BaseModel):
    matches: bool
    reason: str


def _strip_parens(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _and_conjuncts(node: exp.Expression) -> list[exp.Expression]:
    node = _strip_parens(node)
    if isinstance(node, exp.And):
        return _and_conjuncts(node.left) + _and_conjuncts(node.right)
    return [node]


def _is_practice_eq(node: exp.Expression, practice_id: str) -> bool:
    node = _strip_parens(node)
    if not isinstance(node, exp.EQ):
        return False
    for col_side, lit_side in ((node.left, node.right), (node.right, node.left)):
        if (
            isinstance(col_side, exp.Column)
            and col_side.name == "practice_id"
            and isinstance(lit_side, exp.Literal)
            and lit_side.is_string
            and lit_side.name == practice_id
        ):
            return True
    return False


def _has_tenant_scope(main: exp.Select, practice_id: str) -> bool:
    """True solo si `practice_id = '<pid>'` es una conjunción AND obligatoria del
    WHERE de la consulta EXTERNA: no bajo un OR, ni en la proyección/ORDER BY, ni
    únicamente dentro de una subconsulta o JOIN ON. Fail-closed."""
    where = main.args.get("where")
    if where is None:
        return False
    return any(_is_practice_eq(c, practice_id) for c in _and_conjuncts(where.this))


def _clamp_limit(select: exp.Select, cap: int) -> None:
    limit = select.args.get("limit")
    if limit is None:
        select.limit(cap, copy=False)
        return
    try:
        value = int(limit.expression.name)
    except (AttributeError, ValueError):
        select.limit(cap, copy=False)
        return
    if value > cap:
        select.limit(cap, copy=False)


def validate_select(
    sql: str, allowed_tables: frozenset[str], practice_id: str, row_limit: int
) -> ValidationResult:
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as e:  # noqa: BLE001 - parse error de sqlglot
        return ValidationResult(False, sql, f"parse_error: {e}")
    if len(statements) != 1:
        return ValidationResult(False, sql, "no es una sola sentencia")
    root = statements[0]
    if not isinstance(root, exp.Select | exp.With):
        return ValidationResult(False, sql, "no es un SELECT")
    main = root if isinstance(root, exp.Select) else root.this
    if not isinstance(main, exp.Select):
        return ValidationResult(False, sql, "WITH no envuelve un SELECT")
    for forbidden in _FORBIDDEN:
        if root.find(forbidden) is not None:
            return ValidationResult(False, sql, f"contiene {forbidden.__name__}")
    if main.args.get("into") is not None:
        return ValidationResult(False, sql, "SELECT INTO no permitido")
    tables = {t.name for t in root.find_all(exp.Table)}
    extra = tables - allowed_tables
    if extra:
        return ValidationResult(False, sql, f"tablas fuera de allow-list: {sorted(extra)}")
    if not _has_tenant_scope(main, practice_id):
        return ValidationResult(False, sql, "practice_id debe ser conjunción AND del WHERE externo")
    _clamp_limit(main, row_limit)
    return ValidationResult(True, root.sql(dialect="postgres"), "ok")


_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(content: str) -> str:
    """El 12b emite el SELECT como TEXTO PLANO (Gemma local devuelve None con
    tool-calling/structured-output para texto libre); a veces lo envuelve en un
    bloque ```sql. Tomamos el contenido crudo y lo dejamos listo para validar con
    sqlglot, que ES la decodificación restringida real de esta ruta (CLAUDE.md §4)
    — no hay regex sobre JSON, el validador hace el control estructural."""
    s = (content or "").strip()
    m = _SQL_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    return s.rstrip(";").strip()


def _gen_messages(
    question: str, layer: SemanticLayer, practice_id: str, feedback: str
) -> list[tuple[str, str]]:
    system = (
        "Sos un generador de SQL para PostgreSQL de un CRM de prácticas profesionales. "
        "Generá UNA sola consulta SELECT de solo lectura (sin comentarios, sin punto y coma, "
        "sin múltiples sentencias). Usá EXCLUSIVAMENTE estas tablas y columnas:\n"
        f"{layer.schema_context}\n\n"
        "Métricas, dimensiones y sinónimos de negocio (guía):\n"
        f"{layer.semantic_context}\n\n"
        f"OBLIGATORIO: filtrá siempre por practice_id = '{practice_id}'. "
        "Para fechas relativas usá now() y date_trunc (esta semana = "
        "start_at >= date_trunc('week', now()) AND "
        "start_at < date_trunc('week', now()) + interval '7 days'). "
        "Respondé solo el SELECT."
    )
    human = f"Pregunta: {question}"
    if feedback:
        human += f"\n\nIntento anterior fallido: {feedback}"
    return [("system", system), ("human", human)]


def _judge_messages(question: str, sql: str) -> list[tuple[str, str]]:
    system = (
        "Sos un juez. Decidí si la consulta SQL responde la intención de la pregunta. "
        "IMPORTANTE: el SQL SIEMPRE incluye un filtro `practice_id = '...'` y una cláusula "
        "`LIMIT` por política OBLIGATORIA del sistema (aislamiento multi-tenant y cota de filas). "
        "NO los penalices ni los trates como 'de más': son correctos por diseño y no provienen de "
        "la pregunta. Juzgá únicamente si el SELECT (métricas, agregaciones, columnas y los "
        "filtros que provienen de la pregunta) responde lo que se pidió. matches=true si responde."
    )
    return [("system", system), ("human", f"Pregunta: {question}\n\nSQL:\n{sql}")]


async def answer_structured(
    question: str,
    practice_id: str,
    *,
    pool: Any = None,
    gen_llm: Any = None,
    judge_llm: Any = None,
) -> SqlResult:
    settings = get_settings()
    layer = await load_semantic_layer(pool)
    gen_llm = gen_llm or make_llm(settings.ollama_model, temperature=0.0)
    judge = (judge_llm or make_llm("gemma4:e4b", temperature=0.0)).with_structured_output(
        SqlIntentVerdict
    )
    feedback = ""
    last_reason = "sin intentos"
    for _ in range(settings.sql_max_attempts):
        try:
            response = await gen_llm.ainvoke(_gen_messages(question, layer, practice_id, feedback))
            raw_sql = _extract_sql(getattr(response, "content", "") or "")
            vr = validate_select(raw_sql, layer.allowed_tables, practice_id, settings.sql_row_limit)
            if not vr.ok:
                last_reason = vr.reason
                feedback = f"SQL inválido: {vr.reason}. Corregilo."
                continue
            verdict: SqlIntentVerdict = await judge.ainvoke(_judge_messages(question, vr.sql))
            if not verdict.matches:
                last_reason = verdict.reason
                feedback = f"El SQL no respondía la intención: {verdict.reason}."
                continue
            rows, columns = await run_select(
                vr.sql, timeout_ms=settings.sql_timeout_ms, row_limit=settings.sql_row_limit
            )
            return SqlResult(sql=vr.sql, rows=rows, columns=columns, abstained=False, reason="ok")
        except Exception as e:  # noqa: BLE001 - fail-closed: cualquier fallo cuenta como intento
            last_reason = f"error: {e}"
            feedback = "Reintentá con un SELECT simple y válido."
    return SqlResult(sql=None, abstained=True, reason=last_reason)
