"""Состояние сервисов и сводные счётчики для обзора."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from adminapp import config
from adminapp.clients import mdb, mongo_client, pg_query, rds

TIMEOUT = 3.0


def _ok(detail: str = "") -> dict:
    return {"ok": True, "detail": detail}


def _fail(detail: str) -> dict:
    return {"ok": False, "detail": detail[:200]}


def check_postgres() -> dict:
    pg_query("SELECT 1")
    return _ok(pg_query("SHOW server_version")[0]["server_version"].split()[0])


def check_mongo() -> dict:
    mongo_client().admin.command("ping")
    return _ok("v" + mongo_client().server_info()["version"])


def check_redis() -> dict:
    rds().ping()
    return _ok("v" + rds().info("server")["redis_version"])


def check_s3() -> dict:
    from adminapp.storage import s3
    s3().head_bucket(Bucket=config.S3_BUCKET)
    return _ok(f"бакет {config.S3_BUCKET}")


def check_ml() -> dict:
    r = httpx.get(f"{config.ML_URL}/health", timeout=TIMEOUT)
    r.raise_for_status()
    docs = httpx.get(f"{config.ML_URL}/v1/documents", timeout=TIMEOUT).json().get("documents", [])
    return _ok(f"документов в базе знаний: {len(docs)}") if docs else {"ok": True, "warn": True, "detail": "база знаний пуста: /v1/ask будет отвечать 503"}


def check_bot() -> dict:
    r = httpx.get(f"{config.BOT_URL}/internal/health", headers={"x-admin-token": config.API_TOKEN}, timeout=TIMEOUT)
    r.raise_for_status()
    return _ok("внутренний API отвечает")


def check_llm() -> dict:
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=TIMEOUT)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return _ok(", ".join(models) if models else "модель не загружена") if models else {"ok": True, "warn": True, "detail": "ollama запущен, модель не загружена"}
    except httpx.ConnectError:
        return {"ok": True, "warn": True, "detail": "ollama не запущен (профиль llm)"}


def check_kafka() -> dict:
    from adminapp.kafkatools import consumer_lag, list_topics
    topics = [t for t in list_topics() if not t.startswith("__")]
    lag = consumer_lag()
    total = sum(g["lag"] for g in lag)
    detail = f"топиков: {len(topics)}; " + ("; ".join(f"{g['group']}: лаг {g['lag']}" + (f" ({g['note']})" if g["note"] else "") for g in lag) or "нет данных")
    return {"ok": True, "warn": total > 0, "detail": detail}


CHECKS = [
    ("Postgres", check_postgres), ("Mongo", check_mongo), ("Redis", check_redis), ("Kafka", check_kafka),
    ("S3", check_s3), ("ML API", check_ml), ("LLM (ollama)", check_llm), ("Бот", check_bot),
]


def services() -> list[dict]:
    def run(item):
        name, fn = item
        try:
            return {"name": name, **fn()}
        except Exception as exc:   # noqa: BLE001 — любая причина = сервис недоступен
            return {"name": name, **_fail(f"{type(exc).__name__}: {exc}")}
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        return list(pool.map(run, CHECKS))


def counters() -> dict:
    users = {r["role"]: r["n"] for r in pg_query("SELECT r.name AS role, count(*)::int AS n FROM users u JOIN role r ON r.id = u.role_id GROUP BY r.name")}
    db = mdb()
    since24, since7 = datetime.now(timezone.utc) - timedelta(hours=24), datetime.now(timezone.utc) - timedelta(days=7)
    moods = {m: 0 for m in ("positive", "neutral", "negative", "critical")}
    for row in db["events"].aggregate([{"$match": {"ts": {"$gte": since7}}}, {"$group": {"_id": "$mood", "n": {"$sum": 1}}}]):
        if row["_id"] in moods:
            moods[row["_id"]] = row["n"]
    flagged = list(db["stats"].find({"red_flag": True}, {"_id": 0, "uid": 1, "last_reason": 1, "updated_at": 1}).limit(50))
    names = {}
    if flagged:
        for r in pg_query("SELECT id::text AS id, name, surname FROM users WHERE id::text = ANY(%s)", ([f["uid"] for f in flagged],)):
            names[r["id"]] = f"{r['name']} {r['surname']}"
    for f in flagged:
        f["name"] = names.get(f["uid"], f["uid"])
    return {
        "users": users,
        "employees": users.get("employee", 0),
        "tickets_open": db["tickets"].count_documents({"status": "open"}),
        "tickets_total": db["tickets"].count_documents({}),
        "events_24h": db["events"].count_documents({"ts": {"$gte": since24}}),
        "moods_7d": moods,
        "flagged": flagged,
    }


def bot_errors(limit: int = 20) -> list[dict]:
    out = []
    for raw in rds().lrange("bot:errors", 0, limit - 1):
        try:
            out.append(json.loads(raw))
        except ValueError:
            pass
    return out
