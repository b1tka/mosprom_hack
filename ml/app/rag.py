"""RAG: вопрос -> (код, ответ).

200 - ответ найден в базе, либо вопрос не рабочий (HR звать не нужно)
400 - в базе нет информации по рабочему вопросу, нужно вызвать HR
"""
from typing import Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from app import config
from app.llm import get_llm
from app.prompts import (
    NO_INFO_ANSWER, NO_INFO_TAG, NOT_WORK_ANSWER, NOT_WORK_TAG, RAG_SYSTEM_PROMPT,
)


def _retrieve(question: str) -> str:
    """Ищет релевантные чанки и склеивает их в контекст."""
    from app import vectorstore

    if vectorstore.count() == 0:
        raise RuntimeError("Векторная база пуста. Сначала выполните: python -m app.ingest build --from-s3")
    docs = vectorstore.get_vector_store().similarity_search(question, k=config.RAG_TOP_K)
    return "\n\n".join(
        f"[Источник: {d.metadata.get('source', '?')}]\n{d.page_content}" for d in docs
    )


def _generate(system: str, question: str) -> str:
    result = get_llm().invoke([SystemMessage(content=system), HumanMessage(content=question)])
    return (result.content or "").strip()


def ask_rag(question: str) -> Tuple[int, str]:
    """Вход - вопрос, выход - (код, ответ)."""
    question = (question or "").strip()
    if not question:
        return 200, NOT_WORK_ANSWER

    context = _retrieve(question)
    # .replace вместо .format: в тексте контекста могут быть фигурные скобки
    answer = _generate(RAG_SYSTEM_PROMPT.replace("{context}", context), question)

    if NO_INFO_TAG in answer or not answer:
        return 400, NO_INFO_ANSWER
    if NOT_WORK_TAG in answer:
        return 200, NOT_WORK_ANSWER
    return 200, answer
