"""Хранилище статистики пользователей.

STORE_BACKEND=file  - JSON-файл, Redis не нужен (для тестов)
STORE_BACKEND=redis - Redis (только сводка, без истории)
STORE_BACKEND=mongo - MongoDB (продакшен): сводка в stats, история оценок в events; их читает hr_analytics
Интерфейс одинаковый: get(uid), save(uid, stats), lock(uid), add_event(event).
add_event(event) -> False, если событие с таким event_key уже есть (повторная доставка из Kafka не удваивает счётчики).
"""
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from app import config


class FileStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get(self, uid: str) -> Optional[dict]:
        with self._lock:
            return self._load().get(uid)

    def save(self, uid: str, stats: dict) -> None:
        with self._lock:
            data = self._load()
            data[uid] = stats
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    @contextmanager
    def lock(self, uid: str):
        with self._lock:
            yield

    def add_event(self, event: dict) -> bool:
        key = event.get("event_key")
        with self._lock:
            path = self.path + ".events.json"
            events = []
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    events = json.load(f)
            if key and any(e.get("event_key") == key for e in events):
                return False
            events.append({**event, "ts": event["ts"].isoformat()})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            return True


class RedisStore:
    def __init__(self, url: str, prefix: str):
        import redis  # импорт здесь, чтобы без Redis модуль не требовал пакет
        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._prefix = prefix

    def _key(self, uid: str) -> str:
        return f"{self._prefix}{uid}"

    def get(self, uid: str) -> Optional[dict]:
        raw = self._r.get(self._key(uid))
        return json.loads(raw) if raw else None

    def save(self, uid: str, stats: dict) -> None:
        self._r.set(self._key(uid), json.dumps(stats, ensure_ascii=False))

    @contextmanager
    def lock(self, uid: str):
        # API и Kafka-консьюмер могут менять одного пользователя одновременно
        with self._r.lock(f"lock:{self._key(uid)}", timeout=10, blocking_timeout=10):
            yield


    def add_event(self, event: dict) -> bool:
        return True  # истории в Redis нет; для неё STORE_BACKEND=mongo


class MongoStore:
    """Сводка на сотрудника (stats, ключ uid) + история оценок (events) + блокировка на пользователя (locks)."""

    LOCK_TTL = 30       # секунд: зависшая блокировка сама протухает
    LOCK_WAIT = 15

    def __init__(self, url: str, stats_name: str, events_name: str, client=None):
        if client is None:
            from pymongo import MongoClient  # импорт здесь: без Mongo модуль не требует пакет
            client = MongoClient(url, serverSelectionTimeoutMS=5000, tz_aware=True)
        self._client = client
        db = client.get_database()
        self._stats = db[stats_name]
        self._events = db[events_name]
        self._locks = db["locks"]
        self._stats.create_index("uid", unique=True)
        self._events.create_index([("uid", 1), ("ts", 1)])
        # sparse: события бота (без event_key) не конфликтуют, а ключ тикета уникален
        self._events.create_index("event_key", unique=True, sparse=True)

    def get(self, uid: str) -> Optional[dict]:
        return self._stats.find_one({"uid": uid}, {"_id": 0})

    def save(self, uid: str, stats: dict) -> None:
        self._stats.replace_one({"uid": uid}, {**stats, "uid": uid}, upsert=True)

    @contextmanager
    def lock(self, uid: str):
        from pymongo.errors import DuplicateKeyError
        name, token = f"stats:{uid}", uuid.uuid4().hex
        deadline = time.monotonic() + self.LOCK_WAIT
        while True:
            now = datetime.now(timezone.utc)
            try:
                # документ есть и не протух -> фильтр не совпадёт, upsert упрётся в дубль _id -> ждём
                self._locks.update_one(
                    {"_id": name, "expires": {"$lt": now}},
                    {"$set": {"expires": datetime.fromtimestamp(now.timestamp() + self.LOCK_TTL, timezone.utc), "owner": token}},
                    upsert=True,
                )
                break
            except DuplicateKeyError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"не удалось взять блокировку {name}")
                time.sleep(0.05)
        try:
            yield
        finally:
            self._locks.delete_one({"_id": name, "owner": token})

    def add_event(self, event: dict) -> bool:
        from pymongo.errors import DuplicateKeyError
        doc = {k: v for k, v in event.items() if not (k == "event_key" and v is None)}
        try:
            self._events.insert_one(doc)
        except DuplicateKeyError:
            return False
        return True


@lru_cache(maxsize=1)
def get_store():
    if config.STORE_BACKEND == "mongo":
        return MongoStore(config.MONGO_URL, config.MONGO_STATS_COLLECTION, config.MONGO_EVENTS_COLLECTION)
    if config.STORE_BACKEND == "redis":
        return RedisStore(config.REDIS_URL, config.REDIS_KEY_PREFIX)
    return FileStore(config.STATS_FILE)
