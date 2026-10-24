"""Срочные оценки (urgent=true) -> Kafka onboarding.alerts -> бот уведомляет закреплённого HR.

Публикация best-effort: сбой Kafka только пишется в лог и не влияет на оценку и статистику.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from app import config

log = logging.getLogger(__name__)

MAX_TEXT = 300
_producer = None
_lock = threading.Lock()


def _get_producer():
    global _producer
    with _lock:
        if _producer is None:
            from kafka import KafkaProducer  # импорт здесь: без Kafka модуль не требует пакет
            _producer = KafkaProducer(
                bootstrap_servers=config.KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8"),
                request_timeout_ms=5000,
                max_block_ms=5000,
            )
        return _producer


def build_alert(uid: str, source: str, classification: dict, ticket_number: Optional[int] = None,
                text: Optional[str] = None) -> dict:
    alert = {
        "type": "red_flag",
        "uid": uid,
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,                       # bot_question | ticket
        "mood": classification["mood"],
        "reason": classification["reason"],
        "explanation": classification.get("explanation", ""),
    }
    if ticket_number is not None:
        alert["ticket_number"] = ticket_number
    if text:
        alert["text"] = text.strip()[:MAX_TEXT]
    return alert


def publish_red_flag(uid: str, source: str, classification: dict, ticket_number: Optional[int] = None,
                     text: Optional[str] = None) -> bool:
    """True, если алерт ушёл в Kafka. Никогда не бросает исключений."""
    global _producer
    try:
        producer = _get_producer()
        producer.send(config.KAFKA_TOPIC_ALERTS, key=uid, value=build_alert(uid, source, classification, ticket_number, text))
        producer.flush(timeout=5)
        return True
    except Exception:
        log.exception("uid=%s: не удалось отправить алерт red_flag в Kafka", uid)
        with _lock:
            _producer = None   # пересоздадим при следующем алерте
        return False
