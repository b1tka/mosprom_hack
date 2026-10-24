"""Хранилище статистики пользователей.

STORE_BACKEND=file  - JSON-файл, Redis не нужен (для тестов)
STORE_BACKEND=redis - Redis (для продакшена)
Интерфейс у обоих одинаковый: get(uid), save(uid, stats), lock(uid).
"""
import json
import os
import threading
from contextlib import contextmanager
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


@lru_cache(maxsize=1)
def get_store():
    if config.STORE_BACKEND == "redis":
        return RedisStore(config.REDIS_URL, config.REDIS_KEY_PREFIX)
    return FileStore(config.STATS_FILE)
