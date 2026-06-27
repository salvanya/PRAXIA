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
