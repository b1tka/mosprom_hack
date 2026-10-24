import random

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Onboarding ML service")


class RagRequest(BaseModel):
    question: str = Field(min_length=1)


class RagResponse(BaseModel):
    code: int  # 200 — ответ найден, 400 — модель не справилась
    answer: str


def rag(question: str) -> tuple[int, str]:
    """Заглушка RAG: 70% — ответ, 30% — отказ. ML-команда заменяет только эту функцию."""
    if random.random() < 0.7:
        return 200, f"Заглушка: ответ на вопрос «{question}»"
    return 400, "Заглушка: модель не нашла ответ"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/rag", response_model=RagResponse)
def rag_endpoint(req: RagRequest) -> RagResponse:
    code, answer = rag(req.question)
    return RagResponse(code=code, answer=answer)
