"""Подключения к Postgres, Mongo, Redis. Создаются лениво, один раз на процесс."""
from functools import lru_cache

import psycopg
import redis as redis_lib
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pymongo import MongoClient

from adminapp import config


@lru_cache(maxsize=1)
def pg_pool() -> ConnectionPool:
    return ConnectionPool(config.DATABASE_URL, min_size=1, max_size=5, open=True, kwargs={"row_factory": dict_row, "autocommit": True})


def pg_query(sql: str, params=None) -> list[dict]:
    with pg_pool().connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


@lru_cache(maxsize=1)
def mongo_client() -> MongoClient:
    return MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=4000, tz_aware=True)


def mdb():
    return mongo_client().get_database()


@lru_cache(maxsize=1)
def rds() -> redis_lib.Redis:
    return redis_lib.Redis.from_url(config.REDIS_URL, decode_responses=True, socket_timeout=4)
