from io import BytesIO
from typing import TypedDict

from pypdf import PdfReader


class ParsedDoc(TypedDict):
    pages: list[tuple[int | None, str]]


def parse(data: bytes, filename: str) -> ParsedDoc:
    name = filename.lower()
    if name.endswith(".pdf"):
        pages = _parse_pdf(data)
    elif name.endswith((".md", ".markdown", ".txt")):
        pages = [(None, data.decode("utf-8", errors="replace"))]
    else:
        raise ValueError(f"Tipo de archivo no soportado: {filename}")

    if not any(text.strip() for _, text in pages):
        raise ValueError(f"El documento no contiene texto extraíble: {filename}")
    return ParsedDoc(pages=pages)


def _parse_pdf(data: bytes) -> list[tuple[int | None, str]]:
    reader = PdfReader(BytesIO(data))
    return [(i + 1, (page.extract_text() or "")) for i, page in enumerate(reader.pages)]
