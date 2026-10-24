import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from adminapp import auth, config
from adminapp.clients import mdb, pg_query
from adminapp.dbcommon import CONFIRM_WORD, require_confirm
from adminapp.ui import register_nav, render

router = APIRouter()
register_nav("Онбординг", "/onboarding")

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STATE_TITLES = {"pending": "ждёт времени", "running": "выполняется", "waiting": "ждёт ответа", "sent": "отправлен", "skipped": "пропущен", "expired": "просрочен"}
STATUS_TITLES = {"active": "идёт", "paused": "на паузе", "done": "завершён"}
DONE_STATES = ("sent", "skipped", "expired")


def _bot(path: str, payload: dict | None = None) -> tuple[bool, str, dict]:
    """Действие через внутренний HTTP бота -> (успех, сообщение об ошибке, ответ)."""
    if not config.API_TOKEN:
        return False, "ADMIN_API_TOKEN не задан: действия через бота недоступны.", {}
    try:
        r = httpx.post(f"{config.BOT_URL}/internal/onboarding/{path}", json=payload or {}, headers={"x-admin-token": config.API_TOKEN}, timeout=60)
        body = r.json()
    except httpx.HTTPError as exc:
        return False, f"Бот недоступен: {type(exc).__name__}", {}
    except ValueError:
        return False, "Бот вернул не JSON", {}
    if r.status_code != 200:
        return False, f"Бот ответил {r.status_code}: {body.get('error', '')}", body
    if body.get("ok") is False:
        return False, str(body.get("error", "действие не выполнено")), body
    return True, "", body


def _summary(ob: dict) -> dict:
    steps = ob.get("steps", [])
    done = sum(1 for s in steps if s["state"] in DONE_STATES)
    pending = sorted((s for s in steps if s["state"] == "pending"), key=lambda s: s["dueAt"])
    return {
        "ob": ob, "done": done, "total": len(steps), "next": pending[0] if pending else None,
        "waits": len(ob.get("waits", [])), "errors": [s for s in steps if s.get("lastError")],
    }


@router.get("/onboarding", response_class=HTMLResponse)
def listing(request: Request, s: auth.Session = Depends(auth.require_user)):
    obs = list(mdb()["onboarding"].find({}).sort("activatedAt", -1).limit(200))
    have = {o["userId"] for o in obs}
    without = [r for r in pg_query("SELECT u.id::text AS id, u.name, u.surname, t.name AS tag, u.start_date::text AS start_date FROM users u JOIN role r ON r.id = u.role_id LEFT JOIN tag t ON t.id = u.tag_id WHERE r.name = 'employee' ORDER BY u.surname") if r["id"] not in have]
    return render(request, "onboarding.html", {"items": [_summary(o) for o in obs], "without": without, "STATUS_TITLES": STATUS_TITLES})


def _ctx(uid: str, message: str = "", error: str = "") -> dict:
    if not UUID.match(uid):
        raise HTTPException(404)
    users = pg_query("SELECT u.id::text AS id, u.name, u.surname, u.max_id, u.email, t.name AS tag, u.start_date::text AS start_date FROM users u LEFT JOIN tag t ON t.id = u.tag_id WHERE u.id = %s", (uid,))
    if not users:
        raise HTTPException(404, "Сотрудник не найден")
    ob = mdb()["onboarding"].find_one({"userId": uid})
    answers = list(mdb()["survey_answers"].find({"userId": uid}).sort("at", -1).limit(300)) if ob else []
    return {
        "u": users[0], "ob": ob, "sum": _summary(ob) if ob else None, "answers": answers, "message": message, "error": error,
        "STATE_TITLES": STATE_TITLES, "STATUS_TITLES": STATUS_TITLES, "CONFIRM_WORD": CONFIRM_WORD,
        "now": datetime.now(timezone.utc),
    }


@router.get("/onboarding/{uid}", response_class=HTMLResponse)
def detail(request: Request, uid: str, s: auth.Session = Depends(auth.require_user)):
    return render(request, "onboarding_detail.html", _ctx(uid))


def _card(request: Request, uid: str, ok: bool, err: str, success: str):
    return render(request, "_onboarding_card.html", _ctx(uid, success if ok else "", err))


@router.post("/onboarding/{uid}/start", response_class=HTMLResponse)
def start(request: Request, uid: str, date: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    ok, err, body = _bot(f"{uid}/start", {"date": date.strip() or None})
    auth.audit(s, "onboarding.start", uid=uid, ok=ok)
    return _card(request, uid, ok, err, "Сценарий запущен." if body.get("created") else "Сценарий уже был запущен.")


@router.post("/onboarding/{uid}/next", response_class=HTMLResponse)
def next_step(request: Request, uid: str, s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    ok, err, body = _bot(f"{uid}/next")
    auth.audit(s, "onboarding.next", uid=uid, step=body.get("step"), ok=ok)
    return _card(request, uid, ok, err, f"Шаг «{body.get('step')}» выполнен сейчас.")


@router.post("/onboarding/{uid}/shift", response_class=HTMLResponse)
def shift(request: Request, uid: str, days: int = Form(...), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    ok, err, _ = _bot(f"{uid}/shift", {"days": days})
    auth.audit(s, "onboarding.shift", uid=uid, days=days, ok=ok)
    return _card(request, uid, ok, err, f"Будущие шаги сдвинуты на {days:+d} дн.")


@router.post("/onboarding/{uid}/status", response_class=HTMLResponse)
def status(request: Request, uid: str, action: str = Form(...), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid) or action not in ("pause", "resume"):
        raise HTTPException(400)
    ok, err, _ = _bot(f"{uid}/{action}")
    auth.audit(s, f"onboarding.{action}", uid=uid, ok=ok)
    return _card(request, uid, ok, err, "На паузе." if action == "pause" else "Продолжено.")


@router.post("/onboarding/{uid}/startdate", response_class=HTMLResponse)
def start_date(request: Request, uid: str, date: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date.strip()):
        raise HTTPException(400, "Дата в формате ГГГГ-ММ-ДД")
    ok, err, body = _bot(f"{uid}/startdate", {"date": date.strip()})
    auth.audit(s, "onboarding.startdate", uid=uid, date=date.strip(), ok=ok)
    return _card(request, uid, ok, err, f"Дата выхода: {body.get('startDate')}. Расписание пересчитано.")


@router.post("/onboarding/{uid}/reset", response_class=HTMLResponse)
def reset(request: Request, uid: str, confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    require_confirm(confirm)
    n = mdb()["onboarding"].delete_one({"userId": uid}).deleted_count
    a = mdb()["survey_answers"].delete_many({"userId": uid}).deleted_count
    auth.audit(s, "onboarding.reset", uid=uid, onboarding=n, answers=a)
    return _card(request, uid, True, "", f"Онбординг удалён (ответов опросов: {a}). Можно запустить заново.")
