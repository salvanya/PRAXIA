from pathlib import Path

import pytest

from app.ingest.parse import parse

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_markdown():
    data = (FIXTURES / "protocolo.md").read_bytes()
    parsed = parse(data, "protocolo.md")
    assert len(parsed["pages"]) == 1
    page_no, text = parsed["pages"][0]
    assert page_no is None
    assert "primera consulta dura 60 minutos" in text


def test_parse_unsupported_raises():
    with pytest.raises(ValueError):
        parse(b"\x00\x01", "imagen.png")


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse(b"   ", "vacio.md")
