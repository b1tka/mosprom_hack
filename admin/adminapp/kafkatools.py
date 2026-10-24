"""Kafka: топики, лаг групп, последние сообщения (только чтение)."""
from kafka import KafkaAdminClient, KafkaConsumer, TopicPartition

from adminapp import config


def _admin() -> KafkaAdminClient:
    return KafkaAdminClient(bootstrap_servers=config.KAFKA_BOOTSTRAP, request_timeout_ms=5000)


def _group_offsets(admin: KafkaAdminClient, group: str) -> dict:
    """Закоммиченные смещения группы. kafka-python 2.x: list_consumer_group_offsets, 3.x: list_group_offsets -> {группа: {tp: meta}}."""
    if hasattr(admin, "list_consumer_group_offsets"):
        return admin.list_consumer_group_offsets(group)
    return admin.list_group_offsets(group).get(group, {})


def list_topics() -> list[str]:
    admin = _admin()
    try:
        return sorted(admin.list_topics())
    finally:
        admin.close()


def consumer_lag() -> list[dict]:
    """Лаг по группам из KAFKA_GROUPS: сумма (конец топика - закоммиченное смещение). Группа без коммитов считается с нуля."""
    admin = _admin()
    consumer = KafkaConsumer(bootstrap_servers=config.KAFKA_BOOTSTRAP, request_timeout_ms=5000)
    result = []
    try:
        for group, topic in config.KAFKA_GROUPS.items():
            offsets = _group_offsets(admin, group)
            if not offsets:
                tps = [TopicPartition(topic, p) for p in sorted(consumer.partitions_for_topic(topic) or [])]
                begin, end = consumer.beginning_offsets(tps), consumer.end_offsets(tps)
                in_topic = sum(end[tp] - begin[tp] for tp in tps)
                result.append({"group": group, "lag": 0, "partitions": 0, "note": f"нет коммитов, в топике сообщений: {in_topic}"})
                continue
            ends = consumer.end_offsets(list(offsets))
            lag = sum(max(0, ends[tp] - meta.offset) for tp, meta in offsets.items())
            result.append({"group": group, "lag": lag, "partitions": len(offsets), "note": ""})
    finally:
        consumer.close()
        admin.close()
    return result


def topic_info(topic: str) -> dict:
    consumer = KafkaConsumer(bootstrap_servers=config.KAFKA_BOOTSTRAP, request_timeout_ms=5000)
    try:
        parts = consumer.partitions_for_topic(topic) or set()
        tps = [TopicPartition(topic, p) for p in sorted(parts)]
        begin, end = consumer.beginning_offsets(tps), consumer.end_offsets(tps)
        return {"topic": topic, "partitions": [{"p": tp.partition, "begin": begin[tp], "end": end[tp]} for tp in tps]}
    finally:
        consumer.close()


def last_messages(topic: str, limit: int = 20) -> list[dict]:
    """Последние сообщения топика (по limit с каждой партиции, затем общий хвост), без commit и без группы."""
    consumer = KafkaConsumer(bootstrap_servers=config.KAFKA_BOOTSTRAP, request_timeout_ms=5000,
                             enable_auto_commit=False, consumer_timeout_ms=2000)
    out = []
    try:
        parts = consumer.partitions_for_topic(topic) or set()
        tps = [TopicPartition(topic, p) for p in sorted(parts)]
        consumer.assign(tps)
        end = consumer.end_offsets(tps)
        begin = consumer.beginning_offsets(tps)
        for tp in tps:
            consumer.seek(tp, max(begin[tp], end[tp] - limit))
        for rec in consumer:
            out.append({"partition": rec.partition, "offset": rec.offset, "ts": rec.timestamp, "key": rec.key.decode("utf-8", "replace") if rec.key else None,
                        "value": (rec.value or b"").decode("utf-8", "replace")[:4000]})
            if all(consumer.position(tp) >= end[tp] for tp in tps):
                break
    finally:
        consumer.close()
    return sorted(out, key=lambda m: m["ts"], reverse=True)[:limit]
