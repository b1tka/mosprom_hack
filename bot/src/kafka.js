import { Kafka, Partitioners, logLevel } from 'kafkajs';
import { config } from './config.js';

const { brokers, requestsTopic, responsesTopic, groupId } = config.kafka;
const kafka = new Kafka({ clientId: 'onboarding-bot', brokers, logLevel: logLevel.WARN });

const producer = kafka.producer({ createPartitioner: Partitioners.DefaultPartitioner });
const consumer = kafka.consumer({ groupId });

// Сообщение пользователя -> ML-модуль. Ключ = max_id, чтобы порядок сообщений одного пользователя сохранялся.
export async function sendToMl({ maxId, text, role, tag }) {
  await producer.send({
    topic: requestsTopic,
    messages: [{ key: String(maxId), value: JSON.stringify({ maxId, text, role, tag, ts: Date.now() }) }],
  });
}

// Ответы ML-модуля: { maxId, text }
export async function startMlConsumer(onResponse) {
  await consumer.subscribe({ topic: responsesTopic, fromBeginning: false });
  await consumer.run({
    eachMessage: async ({ message }) => {
      try {
        await onResponse(JSON.parse(message.value.toString()));
      } catch (err) {
        console.error('ml response handling failed', err);
      }
    },
  });
}

export async function connectKafka() {
  await producer.connect();
  await consumer.connect();
}

export async function disconnectKafka() {
  await Promise.allSettled([producer.disconnect(), consumer.disconnect()]);
}
