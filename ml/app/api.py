"""FastAPI: POST /v1/ask -> RAG-ответ + фоновое обновление статистики настроения.

Запуск:  uvicorn app.api:app --host 0.0.0.0 --port 8000
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from app.mood import get_stats, process_message
from app.rag import ask_rag

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Модель эмбеддингов (~2 ГБ) грузим при старте, а не на первом вопросе пользователя.
    # LLM здесь не трогаем: она в отдельном сервисе и может быть недоступна, тогда /v1/ask отдаёт 503.
    from app import vectorstore
    vectorstore.get_embeddings()
    log.info("Эмбеддинги загружены, документов в векторной базе: %s", vectorstore.count())
    yield


app = FastAPI(title="Корсик", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Подстройте под ваши существующие модели, если они уже есть
class AskRequest(BaseModel):
    user_id: str
    question: str
    stage: Optional[str] = None  # этап onboarding, если система его передаёт


class AskResponse(BaseModel):
    code: int  # 200 - ответ готов / вопрос не рабочий, 400 - нужен HR
    answer: str


def _update_mood(user_id: str, question: str, stage: Optional[str]) -> None:
    try:
        process_message(user_id, question, stage)
    except Exception:
        log.exception("Не удалось обновить статистику для %s", user_id)


@app.post("/v1/ask", response_model=AskResponse)
def ask(req: AskRequest, background: BackgroundTasks):
    """Вопрос -> ответ модели (RAG)."""
    try:
        code, answer = ask_rag(req.question)
    except Exception:
        log.exception("Ошибка RAG")
        raise HTTPException(503, "model or vector store unavailable")

    # Классификация - второй вызов LLM, делаем после отправки ответа, чтобы не задерживать бота
    background.add_task(_update_mood, req.user_id, req.question, req.stage)
    return AskResponse(code=code, answer=answer)


@app.get("/v1/stats/{user_id}")
def stats(user_id: str):
    """Статистика в формате {uid: {...}} (удобно для отладки и для HR-дашборда)."""
    data = get_stats(user_id)
    if data is None:
        raise HTTPException(404, "no stats for this user")
    return {user_id: data}
