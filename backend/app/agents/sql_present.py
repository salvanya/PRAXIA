import re
from typing import Any

from app.config import get_settings

SQL_EMPTY_MESSAGE = "No encontré resultados para esa consulta."

SYNTH_SYSTEM = (
    "Sos el asistente de una práctica profesional. Respondé en español SOLO con los datos "
    "provistos. No inventes ni calcules números nuevos. Si hay varias filas, podés mostrarlas "
    "en una tabla markdown. Sé breve."
)


def _default_llm() -> Any:
    from app.llm import make_llm

    return make_llm(get_settings().ollama_model, temperature=0.1)


def _fmt(value: Any) -> str:
    """Render una celda; NULL de SQL (None) → vacío, no el literal 'None'."""
    return "" if value is None else str(value)


def render_rows_markdown(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return ""
    cols = columns or list(rows[0].keys())
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |" for r in rows)
    return f"{header}\n{sep}\n{body}"


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def _grounded(answer: str, rows: list[dict]) -> bool:
    cells = [str(v) for r in rows for v in r.values()]
    n = str(len(rows))
    return all(any(num in c for c in cells) or num == n for num in _numbers(answer))


def _deterministic(rows: list[dict], columns: list[str]) -> str:
    cols = columns or list(rows[0].keys())
    if len(rows) == 1 and len(cols) == 1:
        return f"Resultado: {_fmt(list(rows[0].values())[0])}"
    return render_rows_markdown(rows, columns)


async def synthesize_sql_answer(
    question: str, rows: list[dict], columns: list[str], llm: Any = None
) -> str:
    if not rows:
        return SQL_EMPTY_MESSAGE
    llm = llm or _default_llm()
    table = render_rows_markdown(rows, columns)
    messages = [("system", SYNTH_SYSTEM), ("human", f"Pregunta: {question}\n\nDatos:\n{table}")]
    resp = await llm.ainvoke(messages)
    answer = (getattr(resp, "content", "") or "").strip()
    if not answer or not _grounded(answer, rows):
        return _deterministic(rows, columns)
    return answer
