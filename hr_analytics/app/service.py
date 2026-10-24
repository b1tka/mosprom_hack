"""Оркестрация: загрузка данных -> отчёты по ролям -> письма / файлы."""
from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from app.config import Settings
from app.document import Report, to_plain_text
from app.mailer import Mailer, build_message
from app.models import ROLE_DIRECTOR, ROLE_EMPLOYEE, ROLE_HR, ROLE_HR_HEAD
from app.render_html import render_html
from app.render_pdf import render_pdf
from app.reports import Data, build_for_role

log = logging.getLogger(__name__)
ROLES = (ROLE_HR, ROLE_DIRECTOR, ROLE_HR_HEAD)


@dataclass
class Result:
    role: str
    name: str
    email: Optional[str]
    status: str          # sent | saved | skipped | error
    detail: str = ""


def load_data(settings: Settings, demo: bool = False) -> Data:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    if demo:
        from app.sources.demo import demo_data

        people, stats, events = demo_data(now, settings.trend_weeks)
        return Data(people, stats, events, now, settings.trend_weeks)

    from app.sources.mongo import MongoStatsSource
    from app.sources.postgres import PeopleSource

    people = PeopleSource(settings).load_people()
    uids = [p.id for p in people if p.role == ROLE_EMPLOYEE]
    mongo = MongoStatsSource(settings)
    try:
        stats = mongo.load_stats(uids)
        events = mongo.load_events(now - timedelta(weeks=settings.trend_weeks + 1))
    finally:
        mongo.close()
    log.info("Загружено: пользователей %s, статистик %s, событий %s", len(people), len(stats), len(events))
    return Data(people, stats, events, now, settings.trend_weeks)


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE)


def html_for_browser(html: str, charts: dict[str, bytes]) -> str:
    """Для сохранения на диск: cid-картинки -> data-URI, чтобы файл открывался в браузере."""
    for cid, png in charts.items():
        html = html.replace(f"cid:{cid}", "data:image/png;base64," + base64.b64encode(png).decode())
    return html


def save_report(rep: Report, html: str, pdf: bytes, out_dir: Path, who: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{rep.filename}__{_safe(who)}"
    base.with_suffix(".html").write_text(html_for_browser(html, rep.charts), encoding="utf-8")
    base.with_suffix(".pdf").write_bytes(pdf)
    return base.with_suffix(".html")


def run(settings: Settings, roles: Iterable[str] = ROLES, demo: bool = False, send: bool = True,
        out_dir: Optional[Path] = None, data: Optional[Data] = None, mailer: Optional[Mailer] = None) -> list[Result]:
    """Строит отчёты. send=False — только сохраняет HTML+PDF в out_dir (предпросмотр / dry-run)."""
    data = data or load_data(settings, demo)
    generated = data.now.strftime("%d.%m.%Y %H:%M")
    results: list[Result] = []
    mailer = mailer or (Mailer(settings) if send else None)
    first = True
    try:
        for role in roles:
            for person, rep in build_for_role(role, data):
                html = render_html(rep, generated)
                pdf = render_pdf(rep, generated)
                if not send:
                    path = save_report(rep, html, pdf, out_dir or Path(settings.output_dir), person.full_name)
                    results.append(Result(role, person.full_name, person.email, "saved", str(path)))
                    continue
                to = settings.recipient_override or person.email
                if not to:
                    log.warning("У %s (%s) нет e-mail в users — отчёт пропущен", person.full_name, role)
                    results.append(Result(role, person.full_name, None, "skipped", "нет e-mail"))
                    continue
                subject = ("[TEST] " if settings.recipient_override else "") + rep.subject
                msg = build_message(settings, to, subject, to_plain_text(rep), html, rep.charts,
                                    (f"{rep.filename}.pdf", pdf) if settings.attach_pdf else None)
                try:
                    if not first:
                        time.sleep(settings.smtp_pause_sec)   # Яндекс режет частые рассылки
                    first = False
                    mailer.send(msg)
                    results.append(Result(role, person.full_name, to, "sent"))
                    log.info("Отправлено: %s -> %s", role, to)
                except Exception as exc:  # noqa: BLE001 — одно письмо не должно ронять всю рассылку
                    log.error("Ошибка отправки %s: %s", to, exc)
                    results.append(Result(role, person.full_name, to, "error", str(exc)))
    finally:
        if mailer:
            mailer.close()
    return results
