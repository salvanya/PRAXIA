from typing import Any

from app.config import get_settings


def make_llm(model: str, temperature: float = 0.0) -> Any:
    """Factory central de ChatOllama (inferencia local). Apunta siempre al
    base_url de settings; centraliza la construcción que antes estaba repetida
    en router/chitchat/síntesis."""
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model,
        base_url=get_settings().ollama_base_url,
        temperature=temperature,
    )
