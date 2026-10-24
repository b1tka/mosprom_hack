from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from adminapp import auth, config, kafkatools
from adminapp.clients import rds
from adminapp.dbcommon import CONFIRM_WORD, require_confirm
from adminapp.storage import s3
from adminapp.ui import register_nav, render

router = APIRouter(prefix="/db")
register_nav("Базы данных", "/db")

MASKED_PREFIXES = ("admin:sess:", "admin:code:")     # значения сессий и кодов входа не показываем


@router.get("", response_class=HTMLResponse)
def index(request: Request, s: auth.Session = Depends(auth.require_user)):
    return render(request, "db_index.html", {})


# ---------- Redis ----------

@router.get("/redis", response_class=HTMLResponse)
def redis_keys(request: Request, pattern: str = "*", s: auth.Session = Depends(auth.require_user)):
    pattern = pattern.strip() or "*"
    r = rds()
    keys, cursor = [], 0
    while len(keys) < 300:
        cursor, batch = r.scan(cursor=cursor, match=pattern, count=500)
        keys.extend(batch)
        if cursor == 0:
            break
    keys = sorted(set(keys))[:300]
    pipe = r.pipeline()
    for k in keys:
        pipe.type(k)
        pipe.ttl(k)
    meta = pipe.execute()
    items = [{"key": k, "type": meta[2 * i], "ttl": meta[2 * i + 1]} for i, k in enumerate(keys)]
    return render(request, "redis.html", {"items": items, "pattern": pattern, "capped": len(keys) >= 300})


def _redis_value(key: str) -> tuple[str, object]:
    r = rds()
    t = r.type(key)
    if key.startswith(MASKED_PREFIXES):
        return t, "(значение скрыто: сессия или код входа)"
    if t == "string":
        v = r.get(key)
        return t, v if len(v) <= 5000 else v[:5000] + "…"
    if t == "hash":
        return t, r.hgetall(key)
    if t == "list":
        return t, r.lrange(key, 0, 99)
    if t == "set":
        return t, sorted(r.srandmember(key, 100))
    if t == "zset":
        return t, r.zrange(key, 0, 99, withscores=True)
    if t == "stream":
        return t, [(i, d) for i, d in r.xrevrange(key, count=20)]
    return t, None


@router.get("/redis/key", response_class=HTMLResponse)
def redis_key(request: Request, name: str, s: auth.Session = Depends(auth.require_user)):
    if not rds().exists(name):
        raise HTTPException(404, "Ключ не найден")
    t, value = _redis_value(name)
    return render(request, "redis_key.html", {"name": name, "type": t, "value": value, "ttl": rds().ttl(name), "CONFIRM_WORD": CONFIRM_WORD})


@router.post("/redis/delete", response_class=HTMLResponse)
def redis_delete(request: Request, name: str = Form(""), confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    require_confirm(confirm)
    n = rds().delete(name)
    auth.audit(s, "redis.delete", key=name, deleted=n)
    return HTMLResponse(f'<p class="ok">Удалено ключей: {n}. <a href="/db/redis">К ключам</a></p>')


# ---------- Kafka (только чтение) ----------

@router.get("/kafka", response_class=HTMLResponse)
def kafka(request: Request, s: auth.Session = Depends(auth.require_user)):
    error, topics, lag = "", [], []
    try:
        topics = [kafkatools.topic_info(t) for t in kafkatools.list_topics() if not t.startswith("__")]
        lag = kafkatools.consumer_lag()
    except Exception as exc:   # noqa: BLE001
        error = f"Kafka недоступна: {type(exc).__name__}: {str(exc)[:150]}"
    for t in topics:
        t["messages"] = sum(p["end"] - p["begin"] for p in t["partitions"])
    return render(request, "kafka.html", {"topics": topics, "lag": lag, "error": error})


@router.get("/kafka/{topic}", response_class=HTMLResponse)
def kafka_topic(request: Request, topic: str, limit: int = 20, s: auth.Session = Depends(auth.require_user)):
    if topic not in kafkatools.list_topics():
        raise HTTPException(404, "Топик не найден")
    msgs = kafkatools.last_messages(topic, min(max(limit, 1), 100))
    return render(request, "kafka_topic.html", {"topic": topic, "msgs": msgs, "info": kafkatools.topic_info(topic)})


# ---------- S3 ----------

@router.get("/s3", response_class=HTMLResponse)
def s3_list(request: Request, prefix: str = "", s: auth.Session = Depends(auth.require_user)):
    try:
        resp = s3().list_objects_v2(Bucket=config.S3_BUCKET, Prefix=prefix, MaxKeys=300)
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(502, f"S3 недоступен: {type(exc).__name__}")
    items = [{"key": o["Key"], "size": o["Size"], "modified": o["LastModified"]} for o in resp.get("Contents", [])]
    return render(request, "s3.html", {"items": items, "prefix": prefix, "bucket": config.S3_BUCKET, "capped": resp.get("IsTruncated", False), "CONFIRM_WORD": CONFIRM_WORD})


@router.get("/s3/download")
def s3_download(key: str, s: auth.Session = Depends(auth.require_user)):
    try:
        obj = s3().get_object(Bucket=config.S3_BUCKET, Key=key)
    except ClientError:
        raise HTTPException(404, "Объект не найден")
    name = key.rsplit("/", 1)[-1] or "file"
    auth.audit(s, "s3.download", key=key)
    from urllib.parse import quote
    return StreamingResponse(obj["Body"].iter_chunks(65536), media_type="application/octet-stream",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.post("/s3/delete", response_class=HTMLResponse)
def s3_delete(request: Request, key: str = Form(""), confirm: str = Form(""), s: auth.Session = Depends(auth.require_csrf)):
    require_confirm(confirm)
    if not key:
        raise HTTPException(400, "Не указан объект")
    try:
        s3().head_object(Bucket=config.S3_BUCKET, Key=key)
        s3().delete_object(Bucket=config.S3_BUCKET, Key=key)
    except ClientError:
        raise HTTPException(404, "Объект не найден")
    auth.audit(s, "s3.delete", key=key)
    return HTMLResponse('<p class="ok">Объект удалён. <a href="/db/s3">К списку</a></p>')
