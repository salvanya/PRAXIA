# Guardrails PII (Presidio) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir la primera capa de guardrails de PII de Praxia: tag no-destructivo en la ingesta de documentos + redacción destructiva (con placeholders) del texto libre que se persiste vía `log_interaction`, más habilitar notas por `log_interaction type='nota'`.

**Architecture:** Un módulo compartido `app/guardrails/pii.py` envuelve Presidio (spaCy español + reconocedores AR) con imports lazy. La ingesta llama `pii.summarize` (no-destructivo → `documents.pii_summary JSONB`); el proposer `propose_interaction` llama `pii.redact` sobre `summary`/`content` **después** de resolver el cliente y **antes** de armar `proposed_action` (la ConfirmCard muestra el texto redactado; HITL intacto). Fail-closed en escrituras, fail-open en ingesta.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, asyncpg/PostgreSQL, Presidio (`presidio-analyzer`, `presidio-anonymizer`) + spaCy `es_core_news_md`, pytest.

## Global Constraints

- **Local-first · $0:** todo el procesamiento de PII corre in-process (Presidio + spaCy). Cero red saliente en runtime más allá de Ollama/Postgres/Qdrant locales. Única red permitida: bajar el modelo spaCy **una vez** (setup).
- **Commits limpios:** ninguna atribución a Claude (sin `Co-Authored-By`, sin menciones). Autor = el usuario. (CLAUDE.md §6.)
- **Imports de Presidio SIEMPRE lazy** dentro de `pii._engines()` → `import app.guardrails.pii` nunca debe fallar aunque falte presidio/modelo; solo falla `_engines()` → `PiiUnavailable`.
- **Fail-closed en escrituras** (si la redacción se espera pero el motor no está → abstención, no se persiste crudo). **Fail-open en ingesta** (el tag es metadata no-crítica → `None` y sigue).
- **HITL inquebrantable:** la redacción vive en el proposer, detrás del `interrupt`. Ninguna escritura ocurre sin confirmación.
- **Multi-tenant:** `practice_id` en toda query nueva (`get_document`, `set_document_status`).
- **Lint/type/test:** `ruff format` **antes** de `ruff check` (line-length 100; select E,F,I,UP,B). `mypy --config-file backend/pyproject.toml` (disallow_untyped_defs=true, ignore_missing_imports=true → los imports sin stubs de presidio no rompen). Gate: `pytest -m "not llm" -q` verde. El `-m llm` requiere Ollama + ambos modelos + Postgres + Qdrant **+ el modelo spaCy** (si no, `log_interaction` abstiene).
- **Todos los comandos corren desde `backend/`** con el venv del repo: `backend\.venv\Scripts\python`. En PowerShell, ejecutable relativo con `&` o `.\`.
- **JSONB sin codec:** `db.py` no registra codec JSON → al escribir `pii_summary` usar `json.dumps(...)` + cast `$N::jsonb`; al leer, `json.loads(...)`.

---

### Task 1: Config — settings de PII

**Files:**
- Modify: `backend/app/config.py` (clase `Settings`, tras `short_term_history_window`)
- Modify: `backend/.env.example` (agregar las 3 vars; crear el archivo si no existe, con las vars actuales `DATABASE_URL`/`QDRANT_URL`/`OLLAMA_BASE_URL`)
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.pii_redaction_enabled: bool` (default `True`), `Settings.pii_spacy_model: str` (default `"es_core_news_md"`), `Settings.pii_score_threshold: float` (default `0.5`). Accesibles vía `get_settings()`.

- [ ] **Step 1: Write the failing test**

En `backend/tests/test_config.py`, agregar al final:

```python
def test_pii_settings_defaults() -> None:
    get_settings.cache_clear()
    s = get_settings()
    assert s.pii_redaction_enabled is True
    assert s.pii_spacy_model == "es_core_news_md"
    assert s.pii_score_threshold == 0.5


def test_pii_redaction_enabled_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PII_REDACTION_ENABLED", "false")
    get_settings.cache_clear()
    assert get_settings().pii_redaction_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_config.py::test_pii_settings_defaults -q`
Expected: FAIL con `AttributeError: 'Settings' object has no attribute 'pii_redaction_enabled'`.

- [ ] **Step 3: Write minimal implementation**

En `backend/app/config.py`, dentro de `class Settings`, después del bloque `short_term_history_window` (línea ~33) y antes de `# Constants (not from env)`:

```python
    pii_redaction_enabled: bool = True
    pii_spacy_model: str = "es_core_news_md"
    pii_score_threshold: float = 0.5
```

- [ ] **Step 4: Update `.env.example`**

Agregar a `backend/.env.example` (crear si no existe con el resto de vars conocidas):

```dotenv
# Guardrails PII (Presidio). Requiere: python -m spacy download es_core_news_md
PII_REDACTION_ENABLED=true
PII_SPACY_MODEL=es_core_news_md
PII_SCORE_THRESHOLD=0.5
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_config.py -q`
Expected: PASS (todos, incluidos los 3 previos).

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/.env.example backend/tests/test_config.py
git commit -m "feat(config): settings de guardrails PII (enabled, spacy model, threshold)"
```

---

### Task 2: Módulo `guardrails/pii.py` — lógica pura (sin motor)

**Files:**
- Create: `backend/app/guardrails/__init__.py`
- Create: `backend/app/guardrails/pii.py`
- Test: `backend/tests/test_pii.py`

**Interfaces:**
- Consumes: `app.config.get_settings()` (Task 1: `pii_redaction_enabled`, `pii_spacy_model`, `pii_score_threshold`).
- Produces:
  - `pii.PiiUnavailable(RuntimeError)` — el motor no inicializó.
  - `pii.analyze(text: str) -> list[Any]` — spans crudos (llama `_engines()`).
  - `pii.summarize(text: str) -> dict[str, int]` — conteo por tipo; `{}` si `enabled=False`.
  - `pii.redact(text: str) -> tuple[str, dict[str, int]]` — `(texto_redactado, conteo)`; passthrough `(text, {})` si `enabled=False`; **propaga `PiiUnavailable`** si `enabled=True` y el motor falla.
  - `pii.PLACEHOLDERS: dict[str, str]`, `pii.DEFAULT_PLACEHOLDER: str`, `pii._DNI_REGEX: str`, `pii._CUIT_REGEX: str`, `pii._engines()` (lru_cache).

- [ ] **Step 1: Write the failing tests**

Crear `backend/tests/test_pii.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_pii.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.guardrails'`.

- [ ] **Step 3: Write the implementation**

Crear `backend/app/guardrails/__init__.py` **vacío**.

Crear `backend/app/guardrails/pii.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_pii.py -q`
Expected: PASS (7 tests). No requieren presidio instalado (usan disabled/mocked/regex puros).

- [ ] **Step 5: Lint + type**

Run: `& backend\.venv\Scripts\ruff format backend/app/guardrails backend/tests/test_pii.py; & backend\.venv\Scripts\ruff check backend/app/guardrails backend/tests/test_pii.py; & backend\.venv\Scripts\python -m mypy --config-file backend/pyproject.toml backend/app/guardrails`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/guardrails/__init__.py backend/app/guardrails/pii.py backend/tests/test_pii.py
git commit -m "feat(guardrails): módulo pii.py (Presidio) — analyze/summarize/redact + recognizers AR"
```

---

### Task 3: Dependencias Presidio + marker `pii` + test de motor real

**Files:**
- Modify: `backend/requirements.txt` (+presidio)
- Modify: `backend/pyproject.toml` (marker `pii`)
- Modify: `CLAUDE.md` (§2 arranque: instalar presidio + bajar el modelo spaCy)
- Create: `backend/tests/test_pii_engine.py` (marcado `pii`; se salta sin el modelo)

**Interfaces:**
- Consumes: `pii.redact`, `pii.summarize`, `pii._engines`, `pii.PiiUnavailable` (Task 2).
- Produces: marker `pii` registrado; el motor real validado end-to-end (redacta PERSON/EMAIL/PHONE/DNI en español).

- [ ] **Step 1: Add dependencies and download the model**

En `backend/requirements.txt`, agregar tras `Faker==30.*`:

```
presidio-analyzer==2.*
presidio-anonymizer==2.*
```

Instalar + bajar el modelo (una vez; requiere red — permitido por §0):

Run: `& backend\.venv\Scripts\python -m pip install -r backend/requirements.txt`
Run: `& backend\.venv\Scripts\python -m spacy download es_core_news_md`

- [ ] **Step 2: Register the `pii` marker**

En `backend/pyproject.toml`, dentro de `[tool.pytest.ini_options].markers`, agregar:

```toml
    "pii: requires the spaCy model es_core_news_md + presidio (deselect with -m 'not pii')",
```

- [ ] **Step 3: Write the engine test**

Crear `backend/tests/test_pii_engine.py`:

```python
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
```

> Nota de implementación: si algún reconocedor predefinido (EmailRecognizer/PhoneRecognizer) no queda activo para `"es"`, registralo explícitamente en `_engines()` (`analyzer.registry.add_recognizer(EmailRecognizer(supported_language="es"))`, etc.) hasta que estos tests pasen. El test es la verdad de tierra.

- [ ] **Step 4: Update CLAUDE.md setup**

En `CLAUDE.md` §2, en el bloque de "Backend", tras `pip install -r requirements.txt ...`, agregar:

```bash
python -m spacy download es_core_news_md   # guardrails PII (Presidio); una vez
```

Y una nota en §9 Gotchas: *"El `-m llm` ahora también requiere `es_core_news_md`: sin el modelo, `log_interaction` abstiene (fail-closed) y su e2e no abre tarjeta."*

- [ ] **Step 5: Run the engine tests**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_pii_engine.py -m pii -q`
Expected: PASS (3 tests) si el modelo está instalado. Si no, SKIP (verde).

- [ ] **Step 6: Verify the no-llm gate still green (engine tests skip cleanly if needed)**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: PASS (los 244 previos + los nuevos de Task 1/2; los de motor corren o se saltan).

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/pyproject.toml backend/tests/test_pii_engine.py CLAUDE.md
git commit -m "build(guardrails): dependencias Presidio + marker pii + test de motor real"
```

---

### Task 4: Redacción PII en `propose_interaction` (write path)

**Files:**
- Modify: `backend/app/agents/interaction_agent.py`
- Test: `backend/tests/test_interaction_agent.py`

**Interfaces:**
- Consumes: `pii.redact` y `pii.PiiUnavailable` (Task 2).
- Produces: `propose_interaction` redacta `summary`/`content` post-resolución; ante `PiiUnavailable` con `enabled=True` → `ProposalResult(proposed_action=None, abstained=True, reason="pii_unavailable")`. `client_name` (del resolver) queda intacto.

- [ ] **Step 1: Write the failing tests**

En `backend/tests/test_interaction_agent.py`, agregar el import (junto a los existentes, al TOP) y una fixture autouse + tests nuevos:

```python
# (al TOP, con los imports existentes)
import pytest

from app.guardrails import pii
```

```python
# fixture autouse: por defecto la redacción es identidad, así los tests
# existentes (que asertan summary/content crudos) siguen verdes. Los tests de
# redacción la sobreescriben.
@pytest.fixture(autouse=True)
def _identity_redact(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(interaction_agent.pii, "redact", lambda t: (t, {}))


async def test_redacts_summary_and_content(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])

    def _fake_redact(text: str) -> tuple[str, dict]:
        red = text.replace("Ana", "<NOMBRE>").replace("12.345.678", "<DNI>")
        return red, {}

    monkeypatch.setattr(interaction_agent.pii, "redact", _fake_redact)
    llm = FakeGenLLM(
        ProposedInteraction(
            client_name="Ana",
            type="nota",
            summary="Nota sobre Ana",
            content="Ana pasó el DNI 12.345.678",
        )
    )
    result = await interaction_agent.propose_interaction(
        "agregá una nota sobre Ana", "pid", now=NOW, gen_llm=llm
    )
    pa = result.proposed_action
    assert pa is not None
    assert pa["params"]["content"] == "<NOMBRE> pasó el DNI <DNI>"
    assert pa["params"]["summary"] == "Nota sobre <NOMBRE>"
    assert pa["params"]["client_name"] == "Ana López"  # intacto (del resolver)
    assert "<NOMBRE>" in pa["summary"]  # la tarjeta muestra el resumen redactado


async def test_fail_closed_when_pii_unavailable(monkeypatch) -> None:
    _patch_clients(monkeypatch, [{"id": "c1", "full_name": "Ana López"}])

    def _boom(text: str) -> tuple[str, dict]:
        raise pii.PiiUnavailable("no model")

    monkeypatch.setattr(interaction_agent.pii, "redact", _boom)
    llm = FakeGenLLM(ProposedInteraction(client_name="Ana", summary="s", content="c"))
    result = await interaction_agent.propose_interaction(
        "registrá algo de Ana", "pid", now=NOW, gen_llm=llm
    )
    assert result.abstained and result.reason == "pii_unavailable"
    assert result.proposed_action is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_interaction_agent.py::test_fail_closed_when_pii_unavailable -q`
Expected: FAIL — hoy `propose_interaction` no llama a `pii.redact` ni maneja `pii_unavailable` (devuelve `proposed_action`, no abstención).

- [ ] **Step 3: Write the implementation**

En `backend/app/agents/interaction_agent.py`:

Agregar imports (al TOP; ruff isort ordena): `import asyncio` (grupo stdlib) y `from app.guardrails import pii` (grupo first-party).

Agregar la constante junto a `GENERIC_MESSAGE`:

```python
PII_UNAVAILABLE_MESSAGE = (
    "No puedo registrar texto libre ahora mismo: el filtro de datos personales no está "
    "disponible. Avisá al administrador."
)
```

Reemplazar el bloque que arma `params`/`proposed_action` (hoy usa `extracted.summary`/`extracted.content`) por:

```python
    try:
        red_summary, _ = await asyncio.to_thread(pii.redact, extracted.summary)
        red_content, _ = await asyncio.to_thread(pii.redact, extracted.content)
    except pii.PiiUnavailable:
        return ProposalResult(
            proposed_action=None,
            abstained=True,
            message=PII_UNAVAILABLE_MESSAGE,
            reason="pii_unavailable",
        )

    params: dict[str, Any] = {
        "client_id": client["id"],
        "client_name": client["full_name"],
        "type": extracted.type,
        "summary": red_summary,
        "content": red_content,
        "occurred_at": now.isoformat(),
        "source": "agente",
    }
    proposed_action = {
        "kind": "log_interaction",
        "summary": _card_summary(client["full_name"], extracted.type, red_summary),
        "params": params,
    }
    return ProposalResult(proposed_action=proposed_action, abstained=False, message="", reason="ok")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_interaction_agent.py -q`
Expected: PASS (los existentes vía fixture identity + los 2 nuevos).

- [ ] **Step 5: Lint + type**

Run: `& backend\.venv\Scripts\ruff format backend/app/agents/interaction_agent.py backend/tests/test_interaction_agent.py; & backend\.venv\Scripts\ruff check backend/app/agents/interaction_agent.py backend/tests/test_interaction_agent.py; & backend\.venv\Scripts\python -m mypy --config-file backend/pyproject.toml backend/app/agents/interaction_agent.py`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/interaction_agent.py backend/tests/test_interaction_agent.py
git commit -m "feat(interaction): redacción PII destructiva de summary/content en el proposer"
```

---

### Task 5: Notas → `log_interaction` (CLASSIFY_PROMPT)

**Files:**
- Modify: `backend/app/agents/write_tools.py` (`CLASSIFY_PROMPT`)
- Test: `backend/tests/test_write_tools.py`

**Interfaces:**
- Produces: `CLASSIFY_PROMPT` con "nota" catalogada bajo `log_interaction` (ya no bajo `unsupported`). `REGISTRY`/`WRITE_KINDS`/`classify_write_action` sin cambios estructurales.

- [ ] **Step 1: Write the failing test**

En `backend/tests/test_write_tools.py`, agregar:

```python
def test_classify_prompt_moves_nota_to_log_interaction() -> None:
    lines = write_tools.CLASSIFY_PROMPT.splitlines()
    log_line = next(line for line in lines if line.strip().startswith("- log_interaction"))
    unsup_line = next(line for line in lines if line.strip().startswith("- unsupported"))
    assert "nota" in log_line.lower()
    assert "nota" not in unsup_line.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py::test_classify_prompt_moves_nota_to_log_interaction -q`
Expected: FAIL — hoy "nota" está en la línea de `unsupported` y NO como ejemplo de `log_interaction`.

- [ ] **Step 3: Write the implementation**

En `backend/app/agents/write_tools.py`, en `CLASSIFY_PROMPT`:

Cambiar la línea de `log_interaction` (agregar el ejemplo de nota) — de:
```python
    "- log_interaction: registrar/anotar una interacción YA OCURRIDA con un cliente "
    "(sesión, llamada, email, nota, mensaje). "
    'Ej: "registrá que llamé a Ana".\n'
```
a:
```python
    "- log_interaction: registrar/anotar una interacción YA OCURRIDA con un cliente, o agregar "
    "una NOTA o texto libre sobre un cliente (sesión, llamada, email, nota, mensaje). "
    'Ej: "registrá que llamé a Ana", "agregá una nota sobre Juan".\n'
```

Cambiar la línea de `unsupported` (quitar la nota) — de:
```python
    "- unsupported: cualquier OTRA acción de escritura (facturar; agregar/editar una NOTA o texto "
    "libre de un cliente; borrar registros). "
    'Ej: "agregá una nota sobre Juan", "facturá la sesión de Ana".\n'
```
a:
```python
    "- unsupported: cualquier OTRA acción de escritura (facturar; borrar registros). "
    'Ej: "facturá la sesión de Ana", "borrá el registro de Juan".\n'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_write_tools.py -q`
Expected: PASS (el nuevo + los existentes, que no dependen del texto del prompt).

- [ ] **Step 5: Lint**

Run: `& backend\.venv\Scripts\ruff format backend/app/agents/write_tools.py backend/tests/test_write_tools.py; & backend\.venv\Scripts\ruff check backend/app/agents/write_tools.py backend/tests/test_write_tools.py`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents/write_tools.py backend/tests/test_write_tools.py
git commit -m "feat(write_tools): rutear notas a log_interaction (CLASSIFY_PROMPT)"
```

---

### Task 6: Schema + `db.py` — persistir `pii_summary` en `documents`

**Files:**
- Modify: `backend/app/schema.sql` (ALTER documents)
- Modify: `backend/app/db.py` (`import json`; `set_document_status` +`pii_summary`; nuevo `get_document`; `list_documents` +columna)
- Test: `backend/tests/test_schema.py` (columna nueva), `backend/tests/test_db.py` (round-trip)

**Interfaces:**
- Consumes: nada nuevo.
- Produces:
  - `db.set_document_status(document_id, status, page_count=None, *, pii_summary: dict | None = None, practice_id)` — persiste `pii_summary` con COALESCE (None no pisa).
  - `db.get_document(practice_id, document_id) -> dict | None` — incluye `pii_summary` ya deserializado (o `None`).

- [ ] **Step 1: Apply the migration**

En `backend/app/schema.sql`, tras el bloque de `documents` / `content_hash` (línea ~65), agregar:

```sql
-- Guardrails PII (Slice 9): resumen no-destructivo de PII por documento.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS pii_summary JSONB;
```

Aplicar (idempotente):
Run: `& backend\.venv\Scripts\python -c "import asyncio, asyncpg, os; from app.config import get_settings; asyncio.run(__import__('app.db', fromlist=['x']).get_pool())" 2>$null; psql $env:DATABASE_URL -f backend/app/schema.sql`

> Si `psql` no está en PATH, aplicar el `ALTER` vía el runner del repo o `docker compose exec -T postgres psql -U praxia -d praxia -f -` con el contenido. El objetivo: la columna `documents.pii_summary` existe.

- [ ] **Step 2: Write the failing tests**

En `backend/tests/test_schema.py`, agregar:

```python
@pytest.mark.integration
async def test_documents_table_has_pii_summary() -> None:
    pool = await db.get_pool()
    rows = await pool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'"
    )
    cols = {r["column_name"] for r in rows}
    assert "pii_summary" in cols
```

En `backend/tests/test_db.py`, agregar (round-trip real; import `json` no hace falta en el test):

```python
@pytest.mark.integration
async def test_set_and_get_document_pii_summary() -> None:
    from app.config import get_settings

    pid = get_settings().practice_id
    doc_id = await db.insert_document(
        pid, doc_type="protocolo", title="PII round-trip", file_uri="upload://x.md",
        mime_type="text/markdown", content_hash=None,
    )
    await db.set_document_status(
        doc_id, "indexado", page_count=1, pii_summary={"PERSON": 3, "AR_DNI": 1}, practice_id=pid
    )
    doc = await db.get_document(pid, doc_id)
    assert doc is not None
    assert doc["pii_summary"] == {"PERSON": 3, "AR_DNI": 1}
    assert doc["status"] == "indexado"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_db.py::test_set_and_get_document_pii_summary -q`
Expected: FAIL — `set_document_status` no acepta `pii_summary` y `db.get_document` no existe.

- [ ] **Step 4: Write the implementation**

En `backend/app/db.py`:

Agregar `import json` al TOP (grupo stdlib, junto a `import asyncio`).

Reemplazar `set_document_status` por:

```python
async def set_document_status(
    document_id: str,
    status: str,
    page_count: int | None = None,
    *,
    pii_summary: dict[str, int] | None = None,
    practice_id: str,
) -> None:
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE documents
        SET status = $2, page_count = $3, pii_summary = COALESCE($5::jsonb, pii_summary)
        WHERE id = $1 AND practice_id = $4
        """,
        document_id,
        status,
        page_count,
        practice_id,
        json.dumps(pii_summary) if pii_summary is not None else None,
    )
    if result == "UPDATE 0":
        raise RuntimeError(f"set_document_status: no se actualizó el documento {document_id}")
```

Agregar `get_document` (p. ej. tras `list_documents`):

```python
async def get_document(practice_id: str, document_id: str) -> dict[str, Any] | None:
    """Fila del documento (con pii_summary deserializado), o None. Scoped por práctica."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id::text, title, doc_type, status, page_count, pii_summary, ingested_at
        FROM documents WHERE id = $1 AND practice_id = $2
        """,
        document_id,
        practice_id,
    )
    if row is None:
        return None
    doc = dict(row)
    raw = doc.get("pii_summary")
    doc["pii_summary"] = json.loads(raw) if raw else None
    return doc
```

(Opcional, coherencia) en `list_documents`, agregar `pii_summary` al SELECT no es necesario para el slice; se deja fuera para no cambiar el contrato del endpoint `/documents`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_db.py backend/tests/test_schema.py -m integration -q`
Expected: PASS (round-trip + columna). Requiere Postgres up con la migración aplicada.

- [ ] **Step 6: Lint + type**

Run: `& backend\.venv\Scripts\ruff format backend/app/db.py backend/tests/test_db.py backend/tests/test_schema.py; & backend\.venv\Scripts\ruff check backend/app/db.py backend/tests/test_db.py backend/tests/test_schema.py; & backend\.venv\Scripts\python -m mypy --config-file backend/pyproject.toml backend/app/db.py`
Expected: sin errores.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schema.sql backend/app/db.py backend/tests/test_db.py backend/tests/test_schema.py
git commit -m "feat(db): persistir pii_summary en documents (+ get_document) + migración"
```

---

### Task 7: Pipeline de ingesta — tag PII no-destructivo

**Files:**
- Modify: `backend/app/ingest/pipeline.py`
- Test: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `pii.summarize` (Task 2), `db.set_document_status(..., pii_summary=...)` y `db.get_document` (Task 6).
- Produces: `pipeline._safe_pii_summary(text: str) -> dict[str, int] | None` (fail-open); `ingest_document` persiste `pii_summary` en la fila `documents` con content **intacto**.

- [ ] **Step 1: Write the failing tests**

En `backend/tests/test_pipeline.py`, agregar (el unit de fail-open no necesita infra):

```python
from app.ingest import pipeline


async def test_safe_pii_summary_returns_counts(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.pii, "summarize", lambda text: {"PERSON": 2})
    assert await pipeline._safe_pii_summary("Ana y Beto") == {"PERSON": 2}


async def test_safe_pii_summary_fail_open(monkeypatch) -> None:
    def _boom(text: str) -> dict:
        raise pipeline.pii.PiiUnavailable("no model")

    monkeypatch.setattr(pipeline.pii, "summarize", _boom)
    assert await pipeline._safe_pii_summary("Ana") is None  # fail-open, no relanza


@pytest.mark.integration
async def test_ingest_persists_pii_summary(monkeypatch) -> None:
    await vectorstore.ensure_collection()
    monkeypatch.setattr(pipeline.pii, "summarize", lambda text: {"PERSON": 2, "AR_DNI": 1})
    data = b"# Doc PII\nJuan Perez, DNI 12.345.678.\n"
    summary = await ingest_document(data, "pii_doc.md", "protocolo", "Doc PII")
    doc = await db.get_document(os.environ["PRACTICE_ID"], summary["document_id"])
    assert doc is not None
    assert doc["pii_summary"] == {"PERSON": 2, "AR_DNI": 1}
```

Agregar el import que falte al TOP de `test_pipeline.py`: `import pytest` ya está; `from app import db, vectorstore` ya está; `import os` ya está.

- [ ] **Step 2: Run tests to verify they fail**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_pipeline.py::test_safe_pii_summary_fail_open -q`
Expected: FAIL — `pipeline._safe_pii_summary` no existe (`AttributeError`).

- [ ] **Step 3: Write the implementation**

En `backend/app/ingest/pipeline.py`:

Agregar imports al TOP: `import asyncio`, `import logging` (grupo stdlib con `import hashlib`) y `from app.guardrails import pii` (grupo first-party). Agregar tras los imports:

```python
logger = logging.getLogger(__name__)
```

Agregar el helper (p. ej. antes de `_mime`):

```python
async def _safe_pii_summary(text: str) -> dict[str, int] | None:
    """Tag no-destructivo de PII. Fail-open: cualquier fallo → None (no bloquea la ingesta)."""
    try:
        return await asyncio.to_thread(pii.summarize, text)
    except Exception:  # noqa: BLE001 - metadata no-crítica; nunca frena la ingesta
        logger.warning("PII summarize falló; se omite el tag de PII", exc_info=True)
        return None
```

En `ingest_document`, dentro del `try`, reemplazar el bloque de éxito (hoy calcula `page_count` y llama `set_document_status`) por:

```python
        page_count = len(parsed["pages"])
        full_text = "\n".join(text for _, text in parsed["pages"])
        pii_summary = await _safe_pii_summary(full_text)
        await db.set_document_status(
            document_id,
            "indexado",
            page_count=page_count,
            pii_summary=pii_summary,
            practice_id=s.practice_id,
        )
        return DocumentSummary(document_id=document_id, status="indexado", n_chunks=len(chunks))
```

(El content que va a `chunk`/`embeddings`/`vectorstore` **no cambia** → el RAG no degrada.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_pipeline.py -q`
Expected: PASS (unit fail-open/counts sin infra; el integration corre si Postgres/Qdrant están up).

- [ ] **Step 5: Lint + type**

Run: `& backend\.venv\Scripts\ruff format backend/app/ingest/pipeline.py backend/tests/test_pipeline.py; & backend\.venv\Scripts\ruff check backend/app/ingest/pipeline.py backend/tests/test_pipeline.py; & backend\.venv\Scripts\python -m mypy --config-file backend/pyproject.toml backend/app/ingest/pipeline.py`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingest/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat(ingest): tag PII no-destructivo (pii_summary) en el pipeline"
```

---

### Task 8: E2E `-m llm` de redacción + gate final

**Files:**
- Create: `backend/tests/test_guardrails_pii_e2e_llm.py`
- Test: gate completo no-llm + (si el entorno lo permite) `-m "llm or pii"`

**Interfaces:**
- Consumes: el grafo real (`build_graph`), `db`, Ollama + ambos modelos, Postgres, Qdrant, spaCy `es_core_news_md`, `seed_demo`.
- Produces: prueba end-to-end de que una interacción con PII se persiste **redactada** y que la nota rutea a `log_interaction`.

- [ ] **Step 1: Write the e2e test**

Crear `backend/tests/test_guardrails_pii_e2e_llm.py` siguiendo el patrón de `test_action_e2e_llm.py` (leerlo primero para reusar helpers de arranque del grafo con checkpointer y el ciclo interrupt→resume):

```python
import os

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import db
from app.config import get_settings
from app.graph.build import build_graph

pytestmark = [pytest.mark.llm, pytest.mark.pii]


async def _last_interaction_content(pid: str, client_id: str) -> str:
    pool = await db.get_pool()
    row = await pool.fetchrow(
        """
        SELECT content FROM interactions
        WHERE practice_id = $1 AND client_id = $2
        ORDER BY occurred_at DESC LIMIT 1
        """,
        pid,
        client_id,
    )
    return row["content"] if row and row["content"] else ""


async def test_log_interaction_persists_redacted_content() -> None:
    from seed_demo import seed_demo

    await seed_demo()
    pid = get_settings().practice_id
    client = (await db.find_clients_by_name(pid, "", limit=1))[0]
    name = client["full_name"].split()[0]

    graph = build_graph(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "pii-e2e-1"}}
    msg = f"registrá que llamé a {name}, me pasó el DNI 30.111.222"
    await graph.ainvoke(
        {
            "messages": [HumanMessage(content=msg)],
            "practice_id": pid,
            "thread_id": "pii-e2e-1",
            "intent": "",
            "retrieved": [],
            "sources": [],
            "candidate_sql": "",
            "judge_scores": {},
            "proposed_action": None,
            "pending_clarification": None,
        },
        cfg,
    )
    snap = await graph.aget_state(cfg)
    assert snap.tasks and snap.tasks[0].interrupts, "debía abrir tarjeta de confirmación"
    action = snap.tasks[0].interrupts[0].value
    assert action["kind"] == "log_interaction"
    assert "30.111.222" not in action["params"]["content"]  # redactado en la tarjeta
    assert "<DNI>" in action["params"]["content"]

    await graph.ainvoke(Command(resume="confirm"), cfg)
    stored = await _last_interaction_content(pid, client["id"])
    assert "30.111.222" not in stored and "<DNI>" in stored
```

> Ajustá el arranque del grafo/`Command(resume=...)` al patrón exacto de `test_action_e2e_llm.py` (valor de `resume`, forma del estado inicial). El objetivo del assert es no-vacuo: la PII NO está cruda en la tarjeta ni en la fila, y SÍ está el placeholder.

- [ ] **Step 2: Run the e2e (entorno completo)**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests/test_guardrails_pii_e2e_llm.py -m "llm and pii" -q`
Expected: PASS con Ollama + modelos + Postgres + Qdrant + `es_core_news_md`. (Ante hiccup de Ollama, reintentar; no debilitar el assert.)

- [ ] **Step 3: Full no-llm gate (no regresión)**

Run: `& backend\.venv\Scripts\ruff format backend; & backend\.venv\Scripts\ruff check backend; & backend\.venv\Scripts\python -m mypy --config-file backend/pyproject.toml backend/app; & backend\.venv\Scripts\python -m pytest backend/tests -m "not llm" -q`
Expected: ruff/mypy limpios; pytest PASS (244 previos + nuevos; los `pii`-engine corren o se saltan según el modelo).

- [ ] **Step 4: Existing `-m llm` gate (no regresión)**

Run: `& backend\.venv\Scripts\python -m pytest backend/tests -m llm -q`
Expected: los 19 previos + el nuevo verdes (con `es_core_news_md` instalado; sin él, `log_interaction` abstiene → instalar el modelo).

- [ ] **Step 5: Smoke §2 (manual, navegador)**

- Registrar una interacción con PII (*"registrá que llamé a \<cliente\>, DNI 30.111.222"*) → la ConfirmCard muestra `<DNI>`; Confirmar → verificar en DB que `interactions.content` no tiene el número crudo.
- *"agregá una nota sobre \<cliente\>: …"* → rutea a `log_interaction` (tarjeta), redactada.
- Subir un doc con PII → `documents.pii_summary` poblado; el RAG sigue citando con datos reales.
- One-shot de create/cancel/reschedule/update + RAG/SQL/chitchat **no regresionan**; toda escritura pide confirmación.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_guardrails_pii_e2e_llm.py
git commit -m "test(guardrails): e2e llm de redacción PII (tarjeta + fila persistida)"
```

---

## Self-Review

**1. Spec coverage** (cada requisito del spec → task):
- Módulo `guardrails/pii.py` (Presidio, ES, AR, API sync, imports lazy) → **Task 2** (+ motor real **Task 3**).
- Ingesta tag no-destructivo → `documents.pii_summary` → **Task 6** (schema/db) + **Task 7** (pipeline).
- Redacción destructiva en el proposer (ConfirmCard redactada, client_name intacto) → **Task 4**.
- Notas vía `log_interaction type='nota'` (CLASSIFY_PROMPT) → **Task 5**.
- Fail-closed escrituras / fail-open ingesta → **Task 4** (`pii_unavailable`) / **Task 7** (`_safe_pii_summary`).
- Config `PII_*` + `.env.example` → **Task 1**; deps + setup CLAUDE.md → **Task 3**.
- Testing no-llm / `-m pii` / `-m llm` + gates → distribuido; e2e y gate final **Task 8**.
- Multi-tenant (`get_document`/`set_document_status` scoped) → **Task 6**. Sin gaps.

**2. Placeholder scan:** sin "TBD/TODO/etc." Cada step con código real. Las notas ("ajustá al patrón de test_action_e2e_llm.py", "afiná recognizers hasta que el test pase") son guías sobre código concreto ya provisto, no placeholders.

**3. Type consistency:**
- `pii.redact(text) -> tuple[str, dict[str,int]]` — usado en Task 4 con `red_summary, _ = await asyncio.to_thread(pii.redact, ...)`. ✓
- `pii.summarize(text) -> dict[str,int]` — usado en Task 7 (`_safe_pii_summary`) y Task 6/8. ✓
- `pii.PiiUnavailable` — lanzado en Task 2, capturado en Task 4 (`except pii.PiiUnavailable`) y Task 7 (`except Exception`). ✓
- `db.set_document_status(..., *, pii_summary=None, practice_id)` — firma de Task 6 consumida por Task 7 con esos kwargs. ✓
- `db.get_document(practice_id, document_id) -> dict | None` con `pii_summary` deserializado — definida en Task 6, usada en Task 7/8. ✓
- `_safe_pii_summary(text) -> dict|None` — Task 7, consistente. ✓

Sin inconsistencias detectadas.
