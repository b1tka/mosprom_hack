"""Шаблоны, навигация и общие помощники отображения."""
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from adminapp import config
from adminapp.auth import csrf_for

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
NAV: list[tuple[str, str]] = []          # (название, путь); разделы добавляют себя при подключении
MSK = ZoneInfo("Europe/Moscow")


def register_nav(label: str, path: str) -> None:
    if (label, path) not in NAV:
        NAV.append((label, path))


def fmt_dt(value, with_seconds: bool = False) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=ZoneInfo("UTC"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(MSK).strftime("%d.%m.%Y %H:%M:%S" if with_seconds else "%d.%m.%Y %H:%M")


templates.env.filters["dt"] = fmt_dt


def render(request: Request, name: str, ctx: Optional[dict] = None, status_code: int = 200):
    s = getattr(request.state, "session", None)
    base = {"request": request, "session": s, "csrf": csrf_for(s.sid) if s else "", "nav": NAV, "access": config.ACCESS, "path": request.url.path}
    return templates.TemplateResponse(request, name, {**base, **(ctx or {})}, status_code=status_code)
