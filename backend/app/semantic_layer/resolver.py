import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app import db

_MODEL_PATH = Path(__file__).with_name("model.yaml")


@dataclass(frozen=True)
class SemanticLayer:
    schema_context: str
    semantic_context: str
    allowed_tables: frozenset[str]
    allowed_columns: dict[str, frozenset[str]]


_cache: SemanticLayer | None = None


def parse_model_yaml(path: str | None = None) -> dict[str, Any]:
    text = Path(path or _MODEL_PATH).read_text(encoding="utf-8")
    spec: dict[str, Any] = yaml.safe_load(text)
    for key in ("entities", "metrics", "dimensions", "glossary"):
        spec.setdefault(key, {})
    return spec


def allowed_tables_from(spec: dict[str, Any]) -> frozenset[str]:
    tables: set[str] = {e["table"] for e in spec["entities"].values()}
    for dim in spec["dimensions"].values():
        match = re.search(r"\bJOIN\s+(\w+)", dim.get("join", ""), re.IGNORECASE)
        if match:
            tables.add(match.group(1))
    return frozenset(tables)


def render_semantic_context(spec: dict[str, Any]) -> str:
    lines: list[str] = ["Métricas:"]
    for name, m in spec["metrics"].items():
        syn = f" (sinónimos: {', '.join(m['synonyms'])})" if m.get("synonyms") else ""
        lines.append(f"- {name}: {m['sql']} sobre {m['from']}{syn}")
    lines.append("Dimensiones:")
    for name, d in spec["dimensions"].items():
        join = f" [{d['join']}]" if d.get("join") else ""
        lines.append(f"- {name}: {d['sql']}{join}")
    lines.append("Glosario:")
    for term, table in spec["glossary"].items():
        lines.append(f"- {term} → {table}")
    return "\n".join(lines)


async def introspect_columns(pool: Any, tables: frozenset[str]) -> dict[str, frozenset[str]]:
    rows = await pool.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
        "ORDER BY table_name, ordinal_position",
        list(tables),
    )
    acc: dict[str, set[str]] = {}
    for r in rows:
        acc.setdefault(r["table_name"], set()).add(r["column_name"])
    return {t: frozenset(cols) for t, cols in acc.items()}


def _render_schema(columns: dict[str, frozenset[str]]) -> str:
    return "\n".join(f"{t}({', '.join(sorted(columns[t]))})" for t in sorted(columns))


async def load_semantic_layer(pool: Any = None) -> SemanticLayer:
    global _cache
    if _cache is not None:
        return _cache
    spec = parse_model_yaml()
    tables = allowed_tables_from(spec)
    pool = pool or await db.get_pool()
    columns = await introspect_columns(pool, tables)
    _cache = SemanticLayer(
        schema_context=_render_schema(columns),
        semantic_context=render_semantic_context(spec),
        allowed_tables=tables,
        allowed_columns=columns,
    )
    return _cache
