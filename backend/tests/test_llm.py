from app import llm


def test_make_llm_builds_chatollama_with_params():
    obj = llm.make_llm("gemma4:e4b", temperature=0.0)
    assert obj.model == "gemma4:e4b"
    assert obj.temperature == 0.0


def test_make_llm_uses_settings_base_url():
    from app.config import get_settings

    obj = llm.make_llm("gemma4:12b", temperature=0.3)
    assert obj.base_url == get_settings().ollama_base_url
