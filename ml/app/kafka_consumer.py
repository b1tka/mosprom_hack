"""Консьюмер Kafka: сообщения {uid, hr_question, answer, stage} -> статистика.

Запуск отдельным процессом:  python -m app.kafka_consumer
handle_message можно вызывать и без Kafka (см. demo.py kafka).
"""
import json
import logging

from app import config
from app.mood import process_message

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kafka_consumer")


def handle_message(msg: dict):
    uid = str(msg["uid"])
    hr_question = (msg.get("hr_question") or "").strip()
    answer = (msg.get("answer") or "").strip()
    stage = msg.get("stage")

    if not answer:
        log.info("Пустой ответ от %s, пропускаю", uid)
        return None

    # Вопрос HR помогает понять короткие ответы вроде "Да." или "Нормально."
    text = f"Вопрос HR: {hr_question}\nОтвет сотрудника: {answer}" if hr_question else answer
    result = process_message(uid, text, stage)
    log.info("uid=%s -> %s", uid, "обновлено" if result else "классификация не удалась")
    return result


def main() -> None:
    from kafka import KafkaConsumer  # pip install kafka-python

    consumer = KafkaConsumer(
        config.KAFKA_TOPIC,
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        group_id=config.KAFKA_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    for record in consumer:
        try:
            handle_message(record.value)
        except Exception:
            log.exception("Ошибка обработки сообщения offset=%s", record.offset)
        consumer.commit()  # не зацикливаемся на "битом" сообщении


if __name__ == "__main__":
    main()
