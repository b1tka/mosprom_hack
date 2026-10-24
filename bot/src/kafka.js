import { Kafka, Partitioners, logLevel } from 'kafkajs';
import { config } from './config.js';

const { brokers, answersTopic } = config.kafka;
const kafka = new Kafka({ clientId: 'onboarding-bot', brokers, logLevel: logLevel.WARN });

const producer = kafka.producer({ createPartitioner: Partitioners.DefaultPartitioner });

// Ответ сотрудника на вопрос HR -> ML-консьюмер настроения (ml/app/kafka_consumer.py).
// uid = users.id; ключ = uid, чтобы сообщения одного сотрудника шли по порядку.
// Пока не вызывается: опросы HR в боте ещё не реализованы.
export async function sendHrAnswer({ uid, hrQuestion, answer, stage = null }) {
  await producer.send({
    topic: answersTopic,
    messages: [{ key: uid, value: JSON.stringify({ uid, hr_question: hrQuestion, answer, stage }) }],
  });
}

// Полный диалог закрытого тикета -> ML: один вердикт о настроении сотрудника на весь диалог.
// Тот же топик, что и ответы на вопросы HR; отличается полем type. Вложения не передаём, только их число.
// Повторное закрытие переоткрытого тикета шлёт диалог заново (closed_at другой), ML защищён от дублей по ключу тикет+closed_at.
export async function sendTicketDialog(ticket) {
  const uid = String(ticket.employee.id);
  const payload = {
    type: 'ticket',
    uid,
    ticket_number: ticket.number,
    closed_at: ticket.closedAt.toISOString(),
    closed_by: ticket.closedBy,
    stage: null,
    title: ticket.title,
    category: ticket.category,
    messages: ticket.messages.map((m) => ({
      from: m.from, text: m.text ?? '', at: m.at, attachments: (m.attachments ?? []).length,
    })),
  };
  await producer.send({ topic: answersTopic, messages: [{ key: uid, value: JSON.stringify(payload) }] });
}

export const newConsumer = (groupId) => kafka.consumer({ groupId });

export async function connectKafka() {
  await producer.connect();
}

export async function disconnectKafka() {
  await producer.disconnect().catch(() => {});
}
