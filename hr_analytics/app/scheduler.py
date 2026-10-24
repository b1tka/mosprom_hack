"""Мини-планировщик без внешних зависимостей: cron из 5 полей (*, списки, диапазоны, шаги)."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def _field(expr: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in expr.split(","):
        rng, _, step = part.partition("/")
        step_n = int(step) if step else 1
        if rng in ("*", ""):
            a, b = lo, hi
        elif "-" in rng:
            a, b = map(int, rng.split("-"))
        else:
            a = int(rng)
            b = hi if step else a
        out.update(range(a, b + 1, step_n))
    if not out or min(out) < lo or max(out) > hi:
        raise ValueError(f"cron: значение вне диапазона {lo}-{hi}: {expr}")
    return out


def cron_matches(expr: str, dt: datetime) -> bool:
    """minute hour day-of-month month day-of-week (0 или 7 = воскресенье)."""
    f = expr.split()
    if len(f) != 5:
        raise ValueError(f"cron должен состоять из 5 полей: {expr!r}")
    minute, hour, dom, mon, dow = (_field(f[0], 0, 59), _field(f[1], 0, 23), _field(f[2], 1, 31),
                                   _field(f[3], 1, 12), {d % 7 for d in _field(f[4], 0, 7)})
    cron_dow = (dt.weekday() + 1) % 7   # python: пн=0 -> cron: пн=1, вс=0
    dom_any, dow_any = f[2] == "*", f[4] == "*"
    day_ok = (dt.day in dom and cron_dow in dow) if (dom_any or dow_any) else (dt.day in dom or cron_dow in dow)
    return dt.minute in minute and dt.hour in hour and dt.month in mon and day_ok


def loop(jobs: dict[str, tuple[str, Callable[[], None]]], tz: str, stop: Callable[[], bool] = lambda: False,
         sleep: Callable[[float], None] = time.sleep) -> None:
    """jobs = {имя: (cron, функция)}. Каждое задание срабатывает не чаще раза в минуту."""
    zone = ZoneInfo(tz)
    for name, (expr, _) in jobs.items():
        cron_matches(expr, datetime.now(zone))   # валидация при старте
        log.info("Расписание %-10s %s (%s)", name, expr, tz)
    last: dict[str, str] = {}
    while not stop():
        now = datetime.now(zone)
        stamp = now.strftime("%Y%m%d%H%M")
        for name, (expr, fn) in jobs.items():
            if last.get(name) != stamp and cron_matches(expr, now):
                last[name] = stamp
                try:
                    log.info("Запуск задания %s", name)
                    fn()
                except Exception:  # noqa: BLE001
                    log.exception("Задание %s завершилось с ошибкой", name)
        sleep(20)
