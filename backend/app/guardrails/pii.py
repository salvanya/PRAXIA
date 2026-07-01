import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

PLACEHOLDERS: dict[str, str] = {
    "PERSON": "<NOMBRE>",
    "PHONE_NUMBER": "<TELÉFONO>",
    "EMAIL_ADDRESS": "<EMAIL>",
    "AR_DNI": "<DNI>",
    "AR_CUIT": "<CUIT>",
    "LOCATION": "<UBICACIÓN>",
    "CREDIT_CARD": "<TARJETA>",
    "IBAN_CODE": "<IBAN>",
}
DEFAULT_PLACEHOLDER = "<DATO>"

# Entidades pedidas a Presidio. Se excluye DATE_TIME a propósito (las fechas se
# necesitan en el dominio y no son PII sensible).
_ENTITIES = [
    "PERSON",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "LOCATION",
    "CREDIT_CARD",
    "IBAN_CODE",
    "AR_DNI",
    "AR_CUIT",
]

# Reconocedores argentinos (Presidio no los trae). Regex de arranque; el test
# de motor real (Task 3) es la verdad de tierra si hay que afinarlos.
_DNI_REGEX = r"\b\d{1,2}\.?\d{3}\.?\d{3}\b"
_CUIT_REGEX = r"\b\d{2}-?\d{8}-?\d\b"

_warned = False


class PiiUnavailable(RuntimeError):
    """El motor de PII no pudo inicializarse (presidio o modelo spaCy ausente)."""


def _warn_disabled() -> None:
    global _warned
    if not _warned:
        logger.warning(
            "PII_REDACTION_ENABLED=false: se persiste texto SIN redacción de PII (modo dev)."
        )
        _warned = True


@lru_cache(maxsize=1)
def _engines() -> tuple[Any, Any]:
    """Init lazy y pesado. Los imports de presidio/spacy van AQUÍ DENTRO para que
    `import app.guardrails.pii` no falle sin la dependencia; solo falla `_engines()`.
    Devuelve (AnalyzerEngine, AnonymizerEngine) o lanza PiiUnavailable."""
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine
    except Exception as exc:  # noqa: BLE001 - ImportError u otros → motor no disponible
        raise PiiUnavailable(f"presidio no disponible: {exc}") from exc

    s = get_settings()
    try:
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "es", "model_name": s.pii_spacy_model}],
            }
        )
        nlp_engine = provider.create_engine()  # carga el modelo spaCy; falla si no está
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["es"])
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="AR_DNI",
                supported_language="es",
                patterns=[Pattern(name="ar_dni", regex=_DNI_REGEX, score=0.4)],
                context=["dni", "documento"],
            )
        )
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="AR_CUIT",
                supported_language="es",
                patterns=[Pattern(name="ar_cuit", regex=_CUIT_REGEX, score=0.5)],
                context=["cuit", "cuil"],
            )
        )
        anonymizer = AnonymizerEngine()
    except Exception as exc:  # noqa: BLE001 - modelo spaCy ausente / config inválida
        raise PiiUnavailable(f"motor PII no inicializó: {exc}") from exc
    return analyzer, anonymizer


def analyze(text: str) -> list[Any]:
    analyzer, _ = _engines()
    s = get_settings()
    return list(
        analyzer.analyze(
            text=text, language="es", entities=_ENTITIES, score_threshold=s.pii_score_threshold
        )
    )


def _counts(results: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return counts


def summarize(text: str) -> dict[str, int]:
    if not get_settings().pii_redaction_enabled:
        return {}
    return _counts(analyze(text))


def redact(text: str) -> tuple[str, dict[str, int]]:
    if not get_settings().pii_redaction_enabled:
        _warn_disabled()
        return text, {}
    _, anonymizer = _engines()  # lanza PiiUnavailable si el motor está caído (fail-closed arriba)
    results = analyze(text)
    from presidio_anonymizer.entities import OperatorConfig

    operators = {
        et: OperatorConfig("replace", {"new_value": ph}) for et, ph in PLACEHOLDERS.items()
    }
    operators["DEFAULT"] = OperatorConfig("replace", {"new_value": DEFAULT_PLACEHOLDER})
    out = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
    return out.text, _counts(results)
