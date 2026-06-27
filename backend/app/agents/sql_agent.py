from dataclasses import dataclass, field

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

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


class SqlDraft(BaseModel):
    sql: str


class SqlIntentVerdict(BaseModel):
    matches: bool
    reason: str


def _has_practice_filter(root: exp.Expression, practice_id: str) -> bool:
    for eq in root.find_all(exp.EQ):
        col = eq.find(exp.Column)
        lit = eq.find(exp.Literal)
        if col is not None and col.name == "practice_id" and lit is not None:
            if lit.is_string and lit.name == practice_id:
                return True
    return False


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
    tables = {t.name for t in root.find_all(exp.Table)}
    extra = tables - allowed_tables
    if extra:
        return ValidationResult(False, sql, f"tablas fuera de allow-list: {sorted(extra)}")
    if not _has_practice_filter(root, practice_id):
        return ValidationResult(False, sql, "falta filtro practice_id")
    _clamp_limit(main, row_limit)
    return ValidationResult(True, root.sql(dialect="postgres"), "ok")
