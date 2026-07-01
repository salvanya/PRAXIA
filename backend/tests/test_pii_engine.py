import pytest

pytest.importorskip("presidio_analyzer")

from app.config import get_settings  # noqa: E402
from app.guardrails import pii  # noqa: E402


@pytest.fixture(autouse=True)
def _require_engine(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PII_REDACTION_ENABLED", "true")
    get_settings.cache_clear()
    try:
        pii._engines()
    except pii.PiiUnavailable:
        pytest.skip("modelo spaCy es_core_news_md no instalado")


@pytest.mark.pii
def test_redact_real_engine_removes_person_email_dni() -> None:
    out, counts = pii.redact("Llamé a Juan Pérez, DNI 12.345.678, mail juan@x.com")
    assert "Juan Pérez" not in out
    assert "12.345.678" not in out
    assert "juan@x.com" not in out
    assert "<NOMBRE>" in out and "<DNI>" in out and "<EMAIL>" in out
    assert counts.get("PERSON", 0) >= 1


@pytest.mark.pii
def test_redact_real_engine_passthrough_without_pii() -> None:
    text = "el turno es el martes a las diez"
    out, counts = pii.redact(text)
    assert out == text
    assert counts == {}


@pytest.mark.pii
def test_summarize_real_engine_counts() -> None:
    counts = pii.summarize("Juan Pérez y María González, tel 11-2233-4455")
    assert counts.get("PERSON", 0) >= 2


@pytest.mark.pii
def test_redact_real_engine_removes_ar_phone_and_cuit() -> None:
    out, _ = pii.redact("Llamá al 11-2233-4455; el CUIT es 20-12345678-3.")
    assert "11-2233-4455" not in out and "20-12345678-3" not in out
    assert "<TELÉFONO>" in out and "<CUIT>" in out
