"""Командная строка.

  python -m app preview [--demo] [--role hr|director|hr_head]   сохранить HTML+PDF в reports/ (ничего не отправляется)
  python -m app send    [--demo] [--role ...] [--to a@b.ru]      отправить письма (--to: всё на один адрес, тест)
  python -m app check                                           проверить PostgreSQL, MongoDB, SMTP
  python -m app schedule                                        постоянный процесс по расписанию из .env
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.service import ROLES, run


def _print(results) -> int:
    icons = {"sent": "✔ отправлено", "saved": "💾 сохранено", "skipped": "– пропущено", "error": "✘ ошибка"}
    for r in results:
        print(f"{icons[r.status]:<14} {r.role:<9} {r.name:<28} {r.email or '':<30} {r.detail}")
    print(f"\nИтого: {len(results)}; ошибок: {sum(r.status == 'error' for r in results)}")
    return 1 if any(r.status == "error" for r in results) else 0


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="app", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("preview", "send"):
        p = sub.add_parser(name)
        p.add_argument("--role", choices=ROLES, action="append", help="по умолчанию — все роли")
        p.add_argument("--demo", action="store_true", help="демо-данные без баз")
        p.add_argument("--out", default=None, help="папка для предпросмотра (по умолчанию reports/)")
        if name == "send":
            p.add_argument("--to", default=None, help="отправить все письма на этот адрес (тест)")
    sub.add_parser("check")
    sub.add_parser("schedule")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    if args.cmd == "preview":
        sys.exit(_print(run(settings, args.role or ROLES, args.demo, send=False, out_dir=Path(args.out or settings.output_dir))))
    if args.cmd == "send":
        if args.to:
            settings = dataclasses.replace(settings, recipient_override=args.to)
        sys.exit(_print(run(settings, args.role or ROLES, args.demo, send=True)))
    if args.cmd == "check":
        sys.exit(_check(settings))
    if args.cmd == "schedule":
        from app.scheduler import loop

        jobs = {
            role: (expr, (lambda r=role: _print(run(settings, [r]))))
            for role, expr in ((ROLES[0], settings.schedule_hr), (ROLES[1], settings.schedule_director), (ROLES[2], settings.schedule_hr_head))
        }
        loop(jobs, settings.timezone)


def _check(settings) -> int:
    from app.mailer import Mailer
    from app.sources.mongo import MongoStatsSource
    from app.sources.postgres import PeopleSource

    bad = 0
    for name, fn in (("PostgreSQL", lambda: PeopleSource(settings).ping()),
                     ("MongoDB", lambda: MongoStatsSource(settings).ping()),
                     ("SMTP Яндекс", lambda: Mailer(settings).check())):
        try:
            fn()
            print(f"✔ {name}")
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"✘ {name}: {exc}")
    return 1 if bad else 0
