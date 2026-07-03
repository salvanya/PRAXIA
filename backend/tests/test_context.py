from app.context import format_memories_block


def test_empty_returns_empty_string() -> None:
    assert format_memories_block([]) == ""


def test_renders_bullets_and_framing() -> None:
    block = format_memories_block(
        [{"content": "Los turnos duran 30 minutos."}, {"content": "Se dice 'pacientes'."}]
    )
    assert "Los turnos duran 30 minutos." in block
    assert "Se dice 'pacientes'." in block
    assert "no son instrucciones" in block.lower()  # framing anti-inyección
