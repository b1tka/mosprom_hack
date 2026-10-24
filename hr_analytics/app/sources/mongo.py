"""Чтение статистики настроения из MongoDB.

Основной документ — одна запись на сотрудника (формат ML-сервиса):
    {uid, stage, total_requests, moods{positive,neutral,negative,critical},
     reasons{technical_issues,...}, red_flag, last_reason}
Старый формат ветки hr_analytics (request_count / mood / request_tags / reason) тоже понимается.
Имена коллекции и поля с идентификатором задаются в .env, остальное подстраивается в normalize_*.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app.config import Settings
from app.models import MOODS, REASONS, Event, Stats

log = logging.getLogger(__name__)


def _counts(src: Any, keys: Iterable[str]) -> dict:
    out = {k: 0 for k in keys}
    if isinstance(src, dict):
        for k, v in src.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = int(v)  # неизвестные ключи не теряем
    return out


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _uid(doc: dict, uid_field: str) -> Optional[str]:
    for key in (uid_field, "uid", "user_id", "_id"):
        if key and doc.get(key) is not None:
            return str(doc[key])
    return None


def normalize_stats(doc: dict, uid_field: str = "uid") -> Optional[Stats]:
    uid = _uid(doc, uid_field)
    if not uid:
        return None
    moods_src = doc.get("moods") if isinstance(doc.get("moods"), dict) else doc.get("mood")
    reasons_src = doc.get("reasons") if isinstance(doc.get("reasons"), dict) else doc.get("request_tags")
    moods = _counts(moods_src, MOODS)
    reasons = _counts(reasons_src, REASONS)
    total = doc.get("total_requests", doc.get("request_count"))
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        total = sum(moods.values())
    last_reason = doc.get("last_reason") or doc.get("reason") or ""
    return Stats(
        uid=uid,
        stage=doc.get("stage") or None,       # сейчас ML отдаёт null — это нормально
        total=int(total),
        moods=moods,
        reasons=reasons,
        red_flag=bool(doc.get("red_flag", False)),
        last_reason=str(last_reason),
        updated_at=_as_dt(doc.get("updated_at")),
    )


def normalize_event(doc: dict, uid_field: str = "uid") -> Optional[Event]:
    uid = _uid(doc, uid_field)
    ts = _as_dt(doc.get("ts") or doc.get("created_at") or doc.get("timestamp") or doc.get("time"))
    if not uid or ts is None:
        return None
    mood = doc.get("mood")
    reason = doc.get("reason")
    return Event(uid=uid, ts=ts,
                 mood=mood if mood in MOODS else None,
                 reason=reason if reason in REASONS else None,
                 urgent=bool(doc.get("urgent", False)))


class MongoStatsSource:
    def __init__(self, settings: Settings, client: Any = None):
        self.s = settings
        if client is None:
            from pymongo import MongoClient  # импорт тут: для --demo pymongo не нужен

            client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.client = client
        self.db = client[settings.mongo_db]

    def ping(self) -> bool:
        self.client.admin.command("ping")
        return True

    def load_stats(self, uids: Optional[Iterable[str]] = None) -> dict[str, Stats]:
        query: dict = {}
        if uids is not None:
            # uid в Mongo может лежать строкой; ObjectId/UUID-типы покрываются fallback-ом ниже
            query = {self.s.mongo_uid_field: {"$in": list(uids)}}
        out: dict[str, Stats] = {}
        for doc in self.db[self.s.mongo_stats_collection].find(query):
            st = normalize_stats(doc, self.s.mongo_uid_field)
            if st:
                out[st.uid] = st
            else:
                log.warning("Документ статистики без идентификатора пользователя пропущен")
        return out

    def load_events(self, since: datetime) -> list[Event]:
        name = self.s.mongo_events_collection
        if not name:
            return []
        events = []
        for key in ("ts", "created_at", "timestamp"):
            docs = list(self.db[name].find({key: {"$gte": since}}))
            if docs:
                events = [e for e in (normalize_event(d, self.s.mongo_uid_field) for d in docs) if e]
                break
        return events

    def close(self) -> None:
        self.client.close()


def weeks_ago(now: datetime, weeks: int) -> datetime:
    return now - timedelta(weeks=weeks)
