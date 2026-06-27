import pytest

from app.semantic_layer import resolver


def test_parse_model_yaml_has_expected_shape() -> None:
    spec = resolver.parse_model_yaml()
    assert "appointments" in spec["entities"]
    assert "turnos_totales" in spec["metrics"]
    assert "por_profesional" in spec["dimensions"]
    assert spec["glossary"]["paciente"] == "clients"


def test_allowed_tables_includes_entities_and_joined_tables() -> None:
    spec = resolver.parse_model_yaml()
    tables = resolver.allowed_tables_from(spec)
    assert tables == frozenset({"appointments", "clients", "practitioners"})


def test_render_semantic_context_mentions_metrics_and_glossary() -> None:
    spec = resolver.parse_model_yaml()
    ctx = resolver.render_semantic_context(spec)
    assert "turnos_totales" in ctx
    assert "ausencias" in ctx
    assert "paciente" in ctx


@pytest.mark.integration
async def test_load_semantic_layer_introspects_columns() -> None:
    layer = await resolver.load_semantic_layer()
    assert "appointments" in layer.allowed_columns
    assert "practice_id" in layer.allowed_columns["appointments"]
    assert "start_at" in layer.allowed_columns["appointments"]
    assert "appointments(" in layer.schema_context
