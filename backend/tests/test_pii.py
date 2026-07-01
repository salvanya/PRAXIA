import re

import pytest

from app.config import get_settings
from app.guardrails import pii


def _set_enabled(monkeypatch, value: str) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PII_REDACTION_ENABLED", value)
    get_settings.cache_clear()


def test_redact_disabled_is_passthrough(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _set_enabled(monkeypatch, "false")
    text = "Llamé a Juan Pérez, DNI 12.345.678"
    assert pii.redact(text) == (text, {})
    assert pii.summarize(text) == {}


def test_redact_propagates_pii_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _set_enabled(monkeypatch, "true")

    def _boom() -> tuple:  # type: ignore[type-arg]
        raise pii.PiiUnavailable("no model")

    monkeypatch.setattr(pii, "_engines", _boom)
    with pytest.raises(pii.PiiUnavailable):
        pii.redact("Juan Pérez")


def test_summarize_propagates_pii_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _set_enabled(monkeypatch, "true")

    def _boom() -> tuple:  # type: ignore[type-arg]
        raise pii.PiiUnavailable("no model")

    monkeypatch.setattr(pii, "_engines", _boom)
    with pytest.raises(pii.PiiUnavailable):
        pii.summarize("Juan Pérez")


def test_placeholders_cover_core_entities() -> None:
    assert {"PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "AR_DNI", "AR_CUIT"} <= set(pii.PLACEHOLDERS)
    assert pii.PLACEHOLDERS["PERSON"] == "<NOMBRE>"
    assert pii.DEFAULT_PLACEHOLDER == "<DATO>"


def test_ar_dni_regex_matches_dni_not_phone() -> None:
    dni = re.compile(pii._DNI_REGEX)
    assert dni.search("mi DNI es 12.345.678 ok")
    assert dni.search("documento 12345678")
    # un teléfono con guiones NO debe matchear como DNI
    assert not dni.search("11-2233-4455")


def test_ar_cuit_regex_matches_cuit() -> None:
    cuit = re.compile(pii._CUIT_REGEX)
    assert cuit.search("CUIT 20-12345678-3")
    assert cuit.search("20123456783")


def test_ar_phone_regex_matches_phone_not_dni() -> None:
    phone = re.compile(pii._AR_PHONE_REGEX)
    assert phone.search("llamá al 11-2233-4455")
    assert phone.search("cel 351-123-4567")
    # un DNI con puntos NO debe matchear como teléfono
    assert not phone.search("12.345.678")
    # un CUIT (2-8-1) NO debe matchear como teléfono
    assert not phone.search("20-12345678-3")


def test_module_imports_without_presidio() -> None:
    import importlib

    mod = importlib.reload(importlib.import_module("app.guardrails.pii"))
    assert hasattr(mod, "redact") and hasattr(mod, "summarize")
