"""Консьюмер Kafka -> статистика настроения. Два вида сообщений в одном топике:
  {uid, hr_question, answer, stage}                    - ответ сотрудника на вопрос HR (одна реплика)
  {type: "ticket", uid, ticket_number, closed_at, stage, title, messages: [{from, text, at, attachments}]}
                                                      - полный диалог закрытого тикета (один вердикт на диалог)

Запуск отдельным процессом:  python -m app.kafka_consumer
handle_message можно вызывать и без Kafka (см. demo.py kafka).
"""
import json
import logging

from app import config
from app.mood import process_dialog, process_message

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kafka_consumer")


ROLE_TITLES = {"employee": "Сотрудник", "hr": "HR", "bot": "Бот"}
MAX_DIALOG_CHARS = 12000   # в окно модели влезает конец диалога; начало отбрасываем


def build_transcript(messages: list) -> str:
    lines = []
    for m in messages:
        text = (m.get("text") or "").strip()
        n = int(m.get("attachments") or 0)
        if n:
            text = f"{text} [вложений: {n}]".strip()
        if text:
            lines.append(f"{ROLE_TITLES.get(m.get('from'), 'Участник')}: {text}")
    while len(lines) > 1 and sum(len(x) + 1 for x in lines) > MAX_DIALOG_CHARS:
        lines.pop(0)
    return "\n".join(lines)[-MAX_DIALOG_CHARS:]


def handle_ticket(msg: dict):
    uid = str(msg["uid"])
    messages = msg.get("messages") or []
    if not any(m.get("from") == "employee" and (m.get("text") or m.get("attachments")) for m in messages):
        log.info("Тикет %s без реплик сотрудника, пропускаю", msg.get("ticket_number"))
        return None
    transcript = build_transcript(messages)
    if not transcript:
        return None
    number = msg.get("ticket_number")
    # ключ закрытия: повторная доставка не удвоит счётчики, а переоткрытый и снова закрытый тикет учтётся заново
    event_key = f"ticket:{uid}:{number}:{msg.get('closed_at')}"
    result = process_dialog(uid, transcript, msg.get("stage"), number, event_key)
    log.info("uid=%s тикет №%s -> %s", uid, number, "обновлено" if result else "классификация не удалась")
    return result


def handle_message(msg: dict):
    if msg.get("type") == "ticket":
        return handle_ticket(msg)
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
