"""Тестовая админка онбординг-бота: вход по коду из бота, обзор, сотрудники, тикеты, база знаний, управление базами.

Только для тестирования: показывает персональные данные и переписку. Порт публикуется на 127.0.0.1 (SSH-туннель).
"""
import html
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from adminapp import auth, config, status
from adminapp.ui import render, register_nav

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("admin")
config.validate()

app = FastAPI(title="Админка (тест)", docs_url=None, redoc_url=None, openapi_url=None)
# защита от DNS-rebinding: принимаем только запросы с ожидаемым Host (localhost через туннель)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
    return response


@app.exception_handler(auth.NeedLogin)
async def need_login(request: Request, _exc: auth.NeedLogin):
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """HTMX не подставляет ответы 4xx в страницу, поэтому ошибку показываем во «flash»-блоке; обычным запросам — страница с текстом."""
    if request.headers.get("hx-request"):
        return HTMLResponse(f'<p class="err">{html.escape(str(exc.detail))}</p>', status_code=200,
                            headers={"HX-Retarget": "#flash", "HX-Reswap": "innerHTML"})
    if exc.status_code in (403, 404, 400) and "text/html" in request.headers.get("accept", ""):
        return render(request, "error.html", {"code": exc.status_code, "detail": str(exc.detail)}, exc.status_code)
    return await http_exception_handler(request, exc)


# ---------- вход ----------

def _login_page(request: Request, error: str = "", status_code: int = 200):
    return render(request, "login.html", {"error": error, "disabled": config.ACCESS == "off"}, status_code)


def _try_login(request: Request, code: str):
    ip = auth.client_ip(request)
    if config.ACCESS == "off":
        return _login_page(request, "Админка отключена (ADMIN_ACCESS=off).", 403)
    if auth.too_many_failures(ip):
        return _login_page(request, "Слишком много неверных кодов. Подождите 10 минут.", 429)
    payload = auth.exchange_code(code)
    role = auth.current_role(payload["maxId"]) if payload else None
    if not payload or not auth.role_allowed(role):
        auth.register_failure(ip)
        return _login_page(request, "Код неверен, устарел или нет прав. Получите новый: напишите боту /admin.", 401)
    sid = auth.start_session({**payload, "role": role})
    s = auth.Session(sid=sid, max_id=payload["maxId"], name=payload.get("name"), role=role)
    auth.audit(s, "login", ip=ip)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(auth.COOKIE, sid, max_age=config.SESSION_TTL, httponly=True, samesite="strict", path="/")
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _login_page(request)


@app.post("/login")
def login_submit(request: Request, code: str = Form("")):
    return _try_login(request, code)


@app.get("/auth")
def login_link(request: Request, code: str = ""):
    return _try_login(request, code)


@app.post("/logout")
def logout(request: Request, s: auth.Session = Depends(auth.require_csrf)):
    auth.audit(s, "logout")
    auth.end_session(s.sid)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE, path="/")
    return response


# ---------- обзор ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, s: auth.Session = Depends(auth.require_user)):
    return render(request, "dashboard.html", {"services": status.services(), "c": status.counters(), "errors": status.bot_errors()})


@app.get("/status", response_class=HTMLResponse)
def status_partial(request: Request, s: auth.Session = Depends(auth.require_user)):
    return render(request, "_status.html", {"services": status.services(), "errors": status.bot_errors()})


register_nav("Обзор", "/")

from adminapp.routes import db_mongo, db_other, db_pg, employees, kb, tickets  # noqa: E402

app.include_router(employees.router)
app.include_router(tickets.router)
app.include_router(kb.router)
app.include_router(db_other.router)   # регистрирует пункт меню «Базы данных»
app.include_router(db_pg.router)
app.include_router(db_mongo.router)
