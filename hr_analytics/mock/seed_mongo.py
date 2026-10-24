"""Заливает демо-статистику в MongoDB (для проверки отчётов на живой базе).

    python mock/seed_mongo.py            # коллекции stats и events по настройкам .env
Пользователей демо (mock/seed_users.sql) нужно предварительно загрузить в PostgreSQL, uid совпадают с users.id.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import MongoClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.sources import demo  # noqa: E402

s = get_settings()
db = MongoClient(s.mongo_url)[s.mongo_db]
_, stats, events = demo.demo_data()
for st in stats.values():
    doc = {"uid": st.uid, "stage": st.stage, "total_requests": st.total, "moods": st.moods, "reasons": st.reasons,
           "red_flag": st.red_flag, "last_reason": st.last_reason, "updated_at": st.updated_at}
    db[s.mongo_stats_collection].replace_one({"uid": st.uid}, doc, upsert=True)
if s.mongo_events_collection:
    db[s.mongo_events_collection].delete_many({"uid": {"$in": list(stats)}})
    db[s.mongo_events_collection].insert_many(
        [{"uid": e.uid, "ts": e.ts, "mood": e.mood, "reason": e.reason, "urgent": e.urgent} for e in events])
print(f"stats: {len(stats)}, events: {len(events) if s.mongo_events_collection else 'пропущено'}")
