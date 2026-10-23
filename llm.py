"""Клиент локальной модели meno-lite-0.1 (OpenAI-совместимый API)."""
from functools import lru_cache

from langchain_openai import ChatOpenAI

import config


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=config.MENO_BASE_URL,
        api_key=config.MENO_API_KEY,
        model=config.MENO_MODEL,
        temperature=config.LLM_TEMPERATURE,
        timeout=config.LLM_TIMEOUT,
        max_retries=1,
    )
