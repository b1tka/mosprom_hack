import os
import re
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from adminapp import auth, config
from adminapp.storage import s3
from adminapp.ui import register_nav, render

router = APIRouter()
register_nav("База знаний", "/kb")

# те же форматы и лимит, что у бота (bot/src/knowledge.js) и ML (ml/app/ingest.py)
CONTENT_TYPES = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown"}
MAX_FILE_BYTES = 50 * 1024 * 1024
INCOMING_PREFIX = "incoming/"
TEST_UID = "00000000-0000-4000-8000-0000000000ad"   # тестовый вопрос к RAG идёт от этого uid, а не от настоящего сотрудника

_jobs: list[dict] = []        # последние фоновые операции (в памяти процесса)
_lock = threading.Lock()


def _job(kind: str, name: str) -> dict:
    j = {"id": uuid.uuid4().hex[:8], "kind": kind, "name": name, "status": "running", "detail": "", "at": datetime.now(timezone.utc)}
    with _lock:
        _jobs.insert(0, j)
        del _jobs[20:]
    return j


def _finish(j: dict, status: str, detail: str) -> None:
    with _lock:
        j["status"], j["detail"] = status, detail


def _clean_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    return name[:200]


def _ml_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return {400: "текст из файла не извлёкся или формат неверный", 404: "файл не найден", 503: "S3, векторная база или LLM недоступны"}.get(code, f"ML вернул {code}")
    return f"{type(exc).__name__}: {exc}"[:200]


def _list_docs() -> tuple[list[str], str]:
    try:
        r = httpx.get(f"{config.ML_URL}/v1/documents", timeout=10)
        r.raise_for_status()
        return r.json().get("documents", []), ""
    except Exception as exc:   # noqa: BLE001
        return [], f"Не удалось получить список из ML: {type(exc).__name__}"


def _index(j: dict, key: str, name: str) -> None:
    try:
        r = httpx.post(f"{config.ML_URL}/v1/documents", json={"key": key}, timeout=30 * 60)
        r.raise_for_status()
        b = r.json()
        _finish(j, "done", f"{'обновлён' if b.get('replaced') else 'добавлен'}, фрагментов: {b.get('chunks_added')}")
    except Exception as exc:   # noqa: BLE001
        _finish(j, "error", _ml_error(exc))


def _delete(j: dict, name: str) -> None:
    try:
        r = httpx.delete(f"{config.ML_URL}/v1/documents/{quote(name, safe='')}", timeout=10 * 60)
        r.raise_for_status()
        _finish(j, "done", "удалён из S3 и базы знаний")
    except Exception as exc:   # noqa: BLE001
        _finish(j, "error", _ml_error(exc))


def _ctx(message: str = "", error: str = "", answer: dict | None = None) -> dict:
    docs, list_err = _list_docs()
    with _lock:
        jobs = [dict(j) for j in _jobs]
    return {"docs": docs, "list_err": list_err, "jobs": jobs, "message": message, "error": error, "answer": answer,
            "running": any(j["status"] == "running" for j in jobs)}


@router.get("/kb", response_class=HTMLResponse)
def kb(request: Request, s: auth.Session = Depends(auth.require_user)):
    return render(request, "kb.html", _ctx())


@router.get("/kb/panel", response_class=HTMLResponse)
def panel(request: Request, s: auth.Session = Depends(auth.require_user)):
    return render(request, "_kb_panel.html", _ctx())


@router.post("/kb/upload", response_class=HTMLResponse)
def upload(request: Request, background: BackgroundTasks, file: UploadFile = File(...), s: auth.Session = Depends(auth.require_csrf)):
    name = _clean_filename(file.filename)
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in CONTENT_TYPES:
        return render(request, "_kb_panel.html", _ctx(error="Поддерживаются только PDF, TXT и MD."))
    key = f"{INCOMING_PREFIX}{uuid.uuid4()}/{name}"
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_FILE_BYTES:
        return render(request, "_kb_panel.html", _ctx(error="Файл больше 50 МБ."))
    if size == 0:
        return render(request, "_kb_panel.html", _ctx(error="Файл пуст."))
    try:
        s3().upload_fileobj(file.file, config.S3_BUCKET, key, ExtraArgs={"ContentType": CONTENT_TYPES[ext]})
    except Exception as exc:   # noqa: BLE001
        return render(request, "_kb_panel.html", _ctx(error=f"Не удалось загрузить в S3: {type(exc).__name__}"))
    j = _job("добавление", name)
    background.add_task(_index, j, key, name)
    auth.audit(s, "kb.upload", name=name, bytes=size)
    return render(request, "_kb_panel.html", _ctx(message=f"Файл «{name}» загружен, идёт индексация (минуты). Статус ниже."))


@router.post("/kb/delete", response_class=HTMLResponse)
def delete(request: Request, background: BackgroundTasks, name: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    docs, _ = _list_docs()
    if name not in docs:   # удаляем только то, что есть в списке ML
        return render(request, "_kb_panel.html", _ctx(error="Документ не найден в списке."))
    j = _job("удаление", name)
    background.add_task(_delete, j, name)
    auth.audit(s, "kb.delete", name=name)
    return render(request, "_kb_panel.html", _ctx(message=f"Удаляю «{name}»…"))


@router.post("/kb/ask", response_class=HTMLResponse)
def ask(request: Request, question: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    question = question.strip()
    if not question:
        return render(request, "_kb_panel.html", _ctx(error="Введите вопрос."))
    try:
        r = httpx.post(f"{config.ML_URL}/v1/ask", json={"user_id": TEST_UID, "question": question[:2000], "stage": None}, timeout=120)
        r.raise_for_status()
        answer = {**r.json(), "question": question}
        err = ""
    except httpx.HTTPStatusError as exc:
        answer, err = None, "ML вернул 503: LLM или векторная база недоступны." if exc.response.status_code == 503 else f"ML вернул {exc.response.status_code}."
    except Exception as exc:   # noqa: BLE001
        answer, err = None, f"ML недоступен: {type(exc).__name__}"
    auth.audit(s, "kb.ask", chars=len(question), ok=answer is not None)
    return render(request, "_kb_panel.html", _ctx(error=err, answer=answer))
