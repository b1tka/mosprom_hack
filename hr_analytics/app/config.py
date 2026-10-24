"""Настройки сервиса аналитики. Всё берётся из переменных окружения / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # python-dotenv необязателен: в Docker переменные приходят из compose
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _s(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # --- PostgreSQL: users / role / tag (ФИО, роли, e-mail) ---
    database_url: str = field(default_factory=lambda: _s("DATABASE_URL") or (
        "postgresql://{u}:{p}@{h}:{port}/{db}".format(
            u=_s("POSTGRES_USER", "onboarding"),
            p=_s("POSTGRES_PASSWORD", ""),
            h=_s("POSTGRES_HOST", "localhost"),
            port=_s("POSTGRES_PORT", "5432"),
            db=_s("POSTGRES_DB", "onboarding"),
        )
    ))

    # --- MongoDB: статистика настроения и обращений ---
    mongo_url: str = field(default_factory=lambda: _s("MONGO_URL", "mongodb://localhost:27018/onboarding"))
    mongo_db: str = field(default_factory=lambda: _s("MONGO_DB", "onboarding"))
    # одна запись на сотрудника (формат ML: stage, total_requests, moods, reasons, red_flag, last_reason)
    mongo_stats_collection: str = field(default_factory=lambda: _s("MONGO_STATS_COLLECTION", "stats"))
    # поле с users.id в документе статистики (если нет — пробуем uid, user_id, _id)
    mongo_uid_field: str = field(default_factory=lambda: _s("MONGO_UID_FIELD", "uid"))
    # необязательно: история классификаций {uid, ts, mood, reason, urgent} -> графики динамики
    mongo_events_collection: str = field(default_factory=lambda: _s("MONGO_EVENTS_COLLECTION", ""))

    # --- Яндекс Почта (SMTP) ---
    smtp_host: str = field(default_factory=lambda: _s("SMTP_HOST", "smtp.yandex.ru"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 465))
    smtp_security: str = field(default_factory=lambda: _s("SMTP_SECURITY", "ssl").lower())  # ssl | starttls
    smtp_user: str = field(default_factory=lambda: _s("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _s("SMTP_PASSWORD"))
    smtp_from_name: str = field(default_factory=lambda: _s("SMTP_FROM_NAME", "Корсик · Онбординг"))
    smtp_pause_sec: float = field(default_factory=lambda: float(_s("SMTP_PAUSE_SEC", "2")))

    # --- Отчёты ---
    timezone: str = field(default_factory=lambda: _s("REPORT_TZ", "Europe/Moscow"))
    attach_pdf: bool = field(default_factory=lambda: _bool("REPORT_ATTACH_PDF", True))
    # тестовый режим: все письма уходят на этот адрес (в тему добавляется [TEST])
    recipient_override: str = field(default_factory=lambda: _s("REPORT_RECIPIENT_OVERRIDE"))
    trend_weeks: int = field(default_factory=lambda: _int("REPORT_TREND_WEEKS", 8))
    output_dir: str = field(default_factory=lambda: _s("REPORT_OUTPUT_DIR", "reports"))

    # --- Расписание (cron, 5 полей, в часовом поясе REPORT_TZ) ---
    schedule_hr: str = field(default_factory=lambda: _s("SCHEDULE_HR", "0 9 * * 1-5"))
    schedule_director: str = field(default_factory=lambda: _s("SCHEDULE_DIRECTOR", "0 9 * * 1"))
    schedule_hr_head: str = field(default_factory=lambda: _s("SCHEDULE_HR_HEAD", "0 9 * * 1"))


def get_settings() -> Settings:
    return Settings()
