"""FastAPI: POST /v1/ask -> RAG-ответ + фоновое обновление статистики настроения;
POST /v1/documents -> добавить файл из S3 в базу знаний.

Запуск:  uvicorn app.api:app --host 0.0.0.0 --port 8000
"""
import logging
import tempfile
import threading
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


class DocumentRequest(BaseModel):
    key: str  # ключ входящего файла в S3: incoming/<id>/<имя файла>


class DocumentResponse(BaseModel):
    source: str        # имя документа в базе знаний
    chunks_added: int
    replaced: bool     # был ли документ с таким именем (старая версия заменена)


# индексация пишет в общие knowledge_base.json и Chroma — по одному документу за раз
_index_lock = threading.Lock()


@app.post("/v1/documents", response_model=DocumentResponse)
def add_document(req: DocumentRequest):
    """Входящий файл из S3 -> чанки -> векторная база; файл переносится в knowledge/.

    404 — нет такого файла, 400 — неверный ключ / формат или из файла не извлёкся текст (файл удаляется),
    503 — сбой S3 или векторной базы. Может идти минуты (OCR сканов на CPU).
    """
    from app import config, ingest, s3_docs

    key = req.key
    parts = key[len(config.S3_INCOMING_PREFIX):].split("/") if key.startswith(config.S3_INCOMING_PREFIX) else []
    name = parts[-1] if len(parts) == 2 and parts[0] and parts[1] not in ("", ".", "..") else ""
    if not name.lower().endswith(ingest.SUPPORTED):
        raise HTTPException(400, f"key must be {config.S3_INCOMING_PREFIX}<id>/<file{'|'.join(ingest.SUPPORTED)}>")
    try:
        if not s3_docs.exists(key):
            raise HTTPException(404, "file not found in S3")
        with _index_lock, tempfile.TemporaryDirectory() as tmp:
            result = ingest.add_document(s3_docs.download(key, tmp))
            if result["status"] != "success":
                s3_docs.delete_key(key)
                raise HTTPException(400, "no text could be extracted from the file")
            s3_docs.promote(key)
    except HTTPException:
        raise
    except Exception:
        log.exception("Ошибка индексации %s", key)
        raise HTTPException(503, "S3 or vector store unavailable")
    log.info("Документ %s добавлен: %s чанков", name, result["chunks_added"])
    return DocumentResponse(source=result["source"], chunks_added=result["chunks_added"], replaced=result["replaced"])


@app.get("/v1/stats/{user_id}")
def stats(user_id: str):
    """Статистика в формате {uid: {...}} (удобно для отладки и для HR-дашборда)."""
    data = get_stats(user_id)
    if data is None:
        raise HTTPException(404, "no stats for this user")
    return {user_id: data}
