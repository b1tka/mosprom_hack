import re

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from adminapp import auth, config
from adminapp.clients import mdb, pg_query
from adminapp.moodview import REASON_TITLES
from adminapp.ui import register_nav, render

router = APIRouter()
register_nav("Тикеты", "/tickets")

PAGE = 25
OID = re.compile(r"^[0-9a-f]{24}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_REPLY = 3000


def _hr_names() -> dict[str, str]:
    return {r["id"]: f"{r['surname']} {r['name']}" for r in pg_query("SELECT u.id::text AS id, u.name, u.surname FROM users u JOIN role r ON r.id = u.role_id WHERE r.name IN ('hr','hr_head')")}


def _ticket_or_404(tid: str) -> dict:
    if not OID.match(tid):
        raise HTTPException(404)
    t = mdb()["tickets"].find_one({"_id": ObjectId(tid)})
    if not t:
        raise HTTPException(404, "Обращение не найдено")
    return t


@router.get("/tickets", response_class=HTMLResponse)
def tickets(request: Request, status: str = "", hr: str = "", category: str = "", q: str = "", page: int = 1, s: auth.Session = Depends(auth.require_user)):
    match: dict = {}
    if status in ("open", "closed"):
        match["status"] = status
    if UUID.match(hr):
        match["hrId"] = hr
    if category in REASON_TITLES:
        match["category"] = category
    col = mdb()["tickets"]
    needle = q.strip().casefold()
    if needle:
        # $regex с флагом i в Mongo не сворачивает регистр кириллицы, поэтому ищем в Python по лёгкой проекции
        light = col.find(match, {"title": 1, "number": 1, "employee.name": 1, "employee.surname": 1})
        match = {"_id": {"$in": [d["_id"] for d in light if needle in f"{d.get('title', '')} {d['employee']['name']} {d['employee']['surname']}".casefold() or needle == str(d["number"])]}}
    total = col.count_documents(match)
    page = max(1, page)
    items = list(col.aggregate([
        {"$match": match}, {"$sort": {"lastMessageAt": -1}}, {"$skip": (page - 1) * PAGE}, {"$limit": PAGE},
        {"$addFields": {"messageCount": {"$size": {"$ifNull": ["$messages", []]}}}}, {"$project": {"messages": 0}},
    ]))
    return render(request, "tickets.html", {
        "items": items, "total": total, "page": page, "pages": max(1, -(-total // PAGE)), "status": status, "hr": hr, "category": category, "q": q,
        "hr_names": _hr_names(), "REASON_TITLES": REASON_TITLES,
    })


def _view_ctx(t: dict, message: str = "", error: str = "") -> dict:
    names = _hr_names()
    authors = {r["id"]: f"{r['surname']} {r['name']}" for r in pg_query("SELECT id::text AS id, name, surname FROM users WHERE id::text = ANY(%s)", ([m["authorId"] for m in t["messages"] if m.get("authorId")],))} if t["messages"] else {}
    return {"t": t, "hr_names": names, "authors": authors, "message": message, "error": error, "REASON_TITLES": REASON_TITLES}


@router.get("/tickets/{tid}", response_class=HTMLResponse)
def ticket(request: Request, tid: str, s: auth.Session = Depends(auth.require_user)):
    return render(request, "ticket.html", _view_ctx(_ticket_or_404(tid)))


def _view(request: Request, tid: str, message: str = "", error: str = ""):
    return render(request, "_ticket_view.html", _view_ctx(_ticket_or_404(tid), message, error))


def _bot(tid: str, action: str, payload: dict | None = None) -> tuple[bool, str]:
    """Действие через внутренний HTTP бота. -> (успех, текст для пользователя)."""
    if not config.API_TOKEN:
        return False, "ADMIN_API_TOKEN не задан: действия через бота недоступны."
    try:
        r = httpx.post(f"{config.BOT_URL}/internal/tickets/{tid}/{action}", json=payload or {}, headers={"x-admin-token": config.API_TOKEN}, timeout=20)
    except httpx.HTTPError as exc:
        return False, f"Бот недоступен: {type(exc).__name__}"
    if r.status_code == 200:
        return True, ""
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code == 409:
        return False, {"closed": "Обращение закрыто: сообщение не отправлено.", "delivery_failed": "Не удалось доставить сообщение сотруднику."}.get(body.get("status"), {"already closed": "Обращение уже закрыто.", "already open": "Обращение уже открыто."}.get(body.get("error"), "Конфликт состояния."))
    return False, f"Бот ответил {r.status_code}: {body.get('error', '')}"


@router.post("/tickets/{tid}/reply", response_class=HTMLResponse)
def reply(request: Request, tid: str, text: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    _ticket_or_404(tid)
    text = text.strip()
    if not text:
        return _view(request, tid, error="Введите текст ответа.")
    if len(text) > MAX_REPLY:
        return _view(request, tid, error=f"Слишком длинный ответ (до {MAX_REPLY} символов).")
    ok, err = _bot(tid, "reply", {"text": text})
    auth.audit(s, "ticket.reply", ticket=tid, chars=len(text), ok=ok)
    return _view(request, tid, message="Ответ отправлен сотруднику." if ok else "", error=err)


@router.post("/tickets/{tid}/close", response_class=HTMLResponse)
def close(request: Request, tid: str, s: auth.Session = Depends(auth.require_csrf)):
    _ticket_or_404(tid)
    ok, err = _bot(tid, "close")
    auth.audit(s, "ticket.close", ticket=tid, ok=ok)
    return _view(request, tid, message="Обращение закрыто, сотрудник уведомлён." if ok else "", error=err)


@router.post("/tickets/{tid}/reopen", response_class=HTMLResponse)
def reopen(request: Request, tid: str, s: auth.Session = Depends(auth.require_csrf)):
    _ticket_or_404(tid)
    ok, err = _bot(tid, "reopen")
    auth.audit(s, "ticket.reopen", ticket=tid, ok=ok)
    return _view(request, tid, message="Обращение переоткрыто, сотрудник уведомлён." if ok else "", error=err)


@router.post("/tickets/{tid}/assign", response_class=HTMLResponse)
def assign(request: Request, tid: str, hr_id: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    _ticket_or_404(tid)
    rows = pg_query("SELECT u.id::text AS id, u.max_id FROM users u JOIN role r ON r.id = u.role_id WHERE u.id::text = %s AND r.name IN ('hr','hr_head')", (hr_id,)) if UUID.match(hr_id) else []
    if not rows:
        raise HTTPException(400, "HR не найден")
    mdb()["tickets"].update_one({"_id": ObjectId(tid)}, {"$set": {"hrId": rows[0]["id"], "hrMaxId": str(rows[0]["max_id"])}})
    auth.audit(s, "ticket.assign", ticket=tid, hr_id=hr_id)
    return _view(request, tid, message="Обращение переназначено. HR уведомление не отправляется: он увидит тикет в своём списке.")
