import { Redis } from 'ioredis';
import { config } from './config.js';

export const redis = new Redis(config.redisUrl);

const key = (maxId) => `dialog:${maxId}`;

// Состояние диалога: { step: string, data: object }. Хранится в Redis, переживает рестарт бота.
export async function getState(maxId) {
  const raw = await redis.get(key(maxId));
  return raw ? JSON.parse(raw) : { step: 'start', data: {} };
}

export async function setState(maxId, state) {
  await redis.set(key(maxId), JSON.stringify(state));
}

export async function clearState(maxId) {
  await redis.del(key(maxId));
}
