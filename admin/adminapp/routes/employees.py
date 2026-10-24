import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from adminapp import auth
from adminapp.clients import mdb, pg_query, rds
from adminapp.moodview import (
    MOOD_TITLES, REASON_TITLES, RISK_ORDER, RISK_TITLES, build_rows, person_from_row, spark, stats_from_doc, weekly_index,
)
from adminapp.ui import register_nav, render

router = APIRouter()
register_nav("Сотрудники", "/employees")

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")

USERS_SQL = """
SELECT u.id::text AS id, u.name, u.surname, r.name AS role, t.name AS tag, u.director_id::text AS director_id,
       u.hr_id::text AS hr_id, u.email, u.phone, u.max_id, u.created_at
  FROM users u JOIN role r ON r.id = u.role_id LEFT JOIN tag t ON t.id = u.tag_id
"""


def _hr_choices() -> list[dict]:
    return pg_query("SELECT u.id::text AS id, u.name, u.surname, r.name AS role FROM users u JOIN role r ON r.id = u.role_id WHERE r.name IN ('hr','hr_head') ORDER BY u.surname")


def _ticket_counts() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in mdb()["tickets"].aggregate([{"$group": {"_id": {"e": "$employee.id", "s": "$status"}, "n": {"$sum": 1}}}]):
        out.setdefault(row["_id"]["e"], {"open": 0, "closed": 0})[row["_id"]["s"]] = row["n"]
    return out


@router.get("/employees", response_class=HTMLResponse)
def employees(request: Request, q: str = "", risk: str = "", flag: str = "", tag: str = "", s: auth.Session = Depends(auth.require_user)):
    people_rows = pg_query(USERS_SQL + " ORDER BY u.surname, u.name")
    people = {r["id"]: person_from_row(r) for r in people_rows}
    extra = {r["id"]: r for r in people_rows}
    stats = {str(d["uid"]): stats_from_doc(d) for d in mdb()["stats"].find({}, {"_id": 0})}
    rows = build_rows([p for p in people.values() if p.role == "employee"], stats, people)
    tickets = _ticket_counts()
    items = []
    for r in rows:
        e = extra[r.person.id]
        hr = people.get(e["hr_id"]) if e["hr_id"] else None
        items.append({"row": r, "e": e, "hr": hr, "tickets": tickets.get(r.person.id, {"open": 0, "closed": 0}), "flag": bool(r.stats and r.stats.red_flag)})
    ql = q.strip().lower()
    if ql:
        items = [i for i in items if ql in f"{i['row'].person.name} {i['row'].person.surname} {i['e'].get('email') or ''} {i['e'].get('max_id')}".lower()]
    if risk in RISK_ORDER:
        items = [i for i in items if i["row"].risk == risk]
    if flag == "1":
        items = [i for i in items if i["flag"]]
    if tag:
        items = [i for i in items if i["row"].person.tag == tag]
    tags = sorted({r["tag"] for r in people_rows if r["tag"]})
    return render(request, "employees.html", {"items": items, "q": q, "risk": risk, "flag": flag, "tag": tag, "tags": tags,
                                              "RISK_TITLES": RISK_TITLES, "total": len(items)})


def _card_ctx(uid: str) -> dict:
    if not UUID.match(uid):
        raise HTTPException(404)
    rows = pg_query(USERS_SQL + " WHERE u.id = %s", (uid,))
    if not rows:
        raise HTTPException(404, "Сотрудник не найден")
    e = rows[0]
    people = {p["id"]: p for p in pg_query("SELECT id::text AS id, name, surname FROM users WHERE id::text = ANY(%s)", ([x for x in (e["director_id"], e["hr_id"]) if x],))}
    db = mdb()
    doc = db["stats"].find_one({"uid": uid}, {"_id": 0})
    st = stats_from_doc(doc) if doc else None
    events = list(db["events"].find({"uid": uid}, {"_id": 0}).sort("ts", -1).limit(100))
    analysed = build_rows([person_from_row(e)], {uid: st} if st else {}, {})[0]
    tickets = list(db["tickets"].find({"employee.id": uid}, {"messages": 0}).sort("lastMessageAt", -1).limit(50))
    return {
        "e": e, "director": people.get(e["director_id"]), "hr": people.get(e["hr_id"]), "stats": st, "analysed": analysed,
        "events": events[:30], "spark": spark(weekly_index(events)), "tickets": tickets, "hr_choices": _hr_choices(),
        "RISK_TITLES": RISK_TITLES, "MOOD_TITLES": MOOD_TITLES, "REASON_TITLES": REASON_TITLES,
        "reset_info": {k: doc.get(k) for k in ("red_flag_reset_at", "red_flag_reset_by")} if doc else {},
    }


@router.get("/employees/{uid}", response_class=HTMLResponse)
def employee(request: Request, uid: str, s: auth.Session = Depends(auth.require_user)):
    return render(request, "employee.html", _card_ctx(uid))


def _card(request: Request, uid: str, message: str = ""):
    return render(request, "_employee_card.html", {**_card_ctx(uid), "message": message})


@router.post("/employees/{uid}/reset-flag", response_class=HTMLResponse)
def reset_flag(request: Request, uid: str, s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    mdb()["stats"].update_one({"uid": uid}, {"$set": {"red_flag": False, "last_reason": "", "red_flag_reset_at": datetime.now(timezone.utc), "red_flag_reset_by": f"admin:{s.max_id}"}})
    rds().delete(f"alert:{uid}")   # следующая срочная оценка снова уведомит HR сразу
    auth.audit(s, "employee.reset_flag", uid=uid)
    return _card(request, uid, "Красный флаг снят.")


@router.post("/employees/{uid}/hr", response_class=HTMLResponse)
def set_hr(request: Request, uid: str, hr_id: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    if hr_id:
        if not UUID.match(hr_id) or not pg_query("SELECT 1 FROM users u JOIN role r ON r.id = u.role_id WHERE u.id = %s AND r.name IN ('hr','hr_head')", (hr_id,)):
            raise HTTPException(400, "HR не найден")
    pg_query("UPDATE users SET hr_id = %s WHERE id = %s", (hr_id or None, uid))
    auth.audit(s, "employee.set_hr", uid=uid, hr_id=hr_id or None)
    return _card(request, uid, "Закреплённый HR обновлён.")


@router.post("/employees/{uid}/email", response_class=HTMLResponse)
def set_email(request: Request, uid: str, email: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    if not UUID.match(uid):
        raise HTTPException(404)
    email = email.strip()
    if email and not EMAIL.match(email):
        raise HTTPException(400, "Неверный email")
    pg_query("UPDATE users SET email = %s WHERE id = %s", (email or None, uid))
    auth.audit(s, "employee.set_email", uid=uid, email=email or None)
    return _card(request, uid, "Email сохранён.")
