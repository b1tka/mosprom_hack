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

export async function connectKafka() {
  await producer.connect();
}

export async function disconnectKafka() {
  await producer.disconnect().catch(() => {});
}
