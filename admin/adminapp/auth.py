"""Вход по одноразовому коду из бота (/admin), сессии в Redis, CSRF-токен и журнал действий."""
import hmac
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request

from adminapp import config
from adminapp.clients import mdb, pg_query, rds

COOKIE = "admin_sid"
FAIL_LIMIT = 10          # неудачных кодов с одного адреса за FAIL_WINDOW секунд
FAIL_WINDOW = 600


class NeedLogin(Exception):
    pass


@dataclass
class Session:
    sid: str
    max_id: str
    name: Optional[str]
    role: Optional[str]

    @property
    def who(self) -> str:
        return f"{self.name or 'неизвестный'} ({self.max_id})"


def csrf_for(sid: str) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), sid.encode(), hashlib.sha256).hexdigest()[:40]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def too_many_failures(ip: str) -> bool:
    return int(rds().get(f"admin:fail:{ip}") or 0) >= FAIL_LIMIT


def register_failure(ip: str) -> None:
    n = rds().incr(f"admin:fail:{ip}")
    if n == 1:
        rds().expire(f"admin:fail:{ip}", FAIL_WINDOW)


def role_allowed(role: Optional[str]) -> bool:
    return config.ACCESS == "anyone" or (config.ACCESS == "hr" and role in config.HR_ROLES)


def current_role(max_id: str) -> Optional[str]:
    if not str(max_id).isdigit():     # max_id в MAX — число; мусор из Redis не должен ронять запрос
        return None
    rows = pg_query("SELECT r.name AS role FROM users u JOIN role r ON r.id = u.role_id WHERE u.max_id = %s", (int(max_id),))
    return rows[0]["role"] if rows else None


def exchange_code(code: str) -> Optional[dict]:
    """Код -> данные пользователя; код сгорает (GETDEL). None — код неверен или устарел."""
    code = (code or "").strip().upper()
    if not (4 <= len(code) <= 16) or not code.isalnum():
        return None
    raw = rds().getdel(config.CODE_PREFIX + code)
    return json.loads(raw) if raw else None


def start_session(payload: dict) -> str:
    sid = secrets.token_urlsafe(32)
    rds().set(config.SESSION_PREFIX + sid, json.dumps({**payload, "created": datetime.now(timezone.utc).isoformat()}), ex=config.SESSION_TTL)
    return sid


def end_session(sid: str) -> None:
    rds().delete(config.SESSION_PREFIX + sid)


def load_session(request: Request) -> Optional[Session]:
    sid = request.cookies.get(COOKIE)
    if not sid:
        return None
    raw = rds().get(config.SESSION_PREFIX + sid)
    if not raw:
        return None
    data = json.loads(raw)
    role = data.get("role")
    if config.ACCESS == "hr":   # роль могли снять после входа — перепроверяем на каждом запросе
        role = current_role(data["maxId"])
        if not role_allowed(role):
            end_session(sid)
            return None
    elif config.ACCESS != "anyone":
        return None
    return Session(sid=sid, max_id=data["maxId"], name=data.get("name"), role=role)


def require_user(request: Request) -> Session:
    s = load_session(request)
    if s is None:
        raise NeedLogin()
    request.state.session = s
    return s


def require_csrf(request: Request, s: Session = Depends(require_user)) -> Session:
    token = request.headers.get("x-csrf", "")
    if not hmac.compare_digest(token, csrf_for(s.sid)):
        raise HTTPException(403, "CSRF: неверный токен")
    return s


def audit(s: Session, action: str, **details) -> None:
    """Журнал действий в Mongo (admin_audit). Тексты запросов пишутся, секреты — нет."""
    try:
        mdb()["admin_audit"].insert_one({"ts": datetime.now(timezone.utc), "maxId": s.max_id, "name": s.name, "action": action, "details": details})
    except Exception:   # журнал не должен ронять действие
        pass
