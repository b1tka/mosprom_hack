import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from adminapp import auth
from adminapp.clients import pg_query
from adminapp.ui import register_nav, render

router = APIRouter()
register_nav("Персонал", "/staff")

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
ROLES = {"hr": "HR-менеджер", "hr_head": "Руководитель по подбору (hr_head)", "director": "Руководитель подразделения"}
MAX_BIGINT = 9223372036854775807


def _staff() -> list[dict]:
    return pg_query(
        "SELECT u.id::text AS id, u.name, u.surname, r.name AS role, u.max_id, u.email, u.created_at "
        "FROM users u JOIN role r ON r.id = u.role_id WHERE r.name = ANY(%s) ORDER BY r.name, u.surname, u.name",
        (list(ROLES),),
    )


def _view(request: Request, message: str = "", error: str = ""):
    return render(request, "_staff.html", {"staff": _staff(), "ROLES": ROLES, "message": message, "error": error})


def _max_id(raw: str) -> int:
    raw = raw.strip()
    if not raw.isdigit() or not 0 < int(raw) <= MAX_BIGINT:
        raise HTTPException(400, "max_id — положительное целое число (узнать: написать боту /id)")
    return int(raw)


def _free(max_id: int, except_uid: str | None = None) -> None:
    taken = pg_query("SELECT u.id::text AS id, u.name, u.surname FROM users u WHERE u.max_id = %s", (max_id,))
    if taken and taken[0]["id"] != except_uid:
        raise HTTPException(400, f"max_id {max_id} уже привязан к {taken[0]['surname']} {taken[0]['name']}")


@router.get("/staff", response_class=HTMLResponse)
def staff(request: Request, s: auth.Session = Depends(auth.require_user)):
    return render(request, "staff.html", {"staff": _staff(), "ROLES": ROLES, "message": "", "error": ""})


@router.post("/staff/add", response_class=HTMLResponse)
def add(request: Request, name: str = Form(...), surname: str = Form(...), role: str = Form(...), max_id: str = Form(...), email: str = Form(""),
        s: auth.Session = Depends(auth.require_csrf)):
    name, surname, email = name.strip(), surname.strip(), email.strip()
    if role not in ROLES:
        raise HTTPException(400, "Роль: hr, hr_head или director")
    if not (1 <= len(name) <= 100 and 1 <= len(surname) <= 100):
        raise HTTPException(400, "Имя и фамилия обязательны (до 100 символов)")
    if email and not EMAIL.match(email):
        raise HTTPException(400, "Неверный email")
    mid = _max_id(max_id)
    _free(mid)
    row = pg_query(
        "INSERT INTO users (name, surname, role_id, max_id, email) SELECT %s, %s, id, %s, %s FROM role WHERE name = %s RETURNING id::text AS id",
        (name, surname, mid, email or None, role),
    )
    if not row:
        raise HTTPException(500, "Роль не найдена в справочнике role")
    auth.audit(s, "staff.add", uid=row[0]["id"], role=role, max_id=mid)
    return _view(request, f"{surname} {name} добавлен(а) как {role}. Теперь этот аккаунт MAX получает в боте /menu.")


@router.post("/staff/{uid}/max-id", response_class=HTMLResponse)
def set_max_id(request: Request, uid: str, max_id: str = Form(...), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid) or not pg_query("SELECT 1 FROM users u JOIN role r ON r.id = u.role_id WHERE u.id = %s AND r.name = ANY(%s)", (uid, list(ROLES))):
        raise HTTPException(404, "Сотрудник персонала не найден")
    mid = _max_id(max_id)
    _free(mid, uid)
    pg_query("UPDATE users SET max_id = %s WHERE id = %s", (mid, uid))
    auth.audit(s, "staff.set_max_id", uid=uid, max_id=mid)
    return _view(request, "max_id привязан.")
