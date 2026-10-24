import { redis } from './state.js';

// Последние ошибки бота в Redis (кольцевой буфер) — их показывает тестовая админка.
const KEY = 'bot:errors';
const KEEP = 100;

const original = console.error.bind(console);

function describe(args) {
  return args.map((a) => (a instanceof Error ? `${a.message}${a.code ? ` [${a.code}]` : ''}` : typeof a === 'string' ? a : JSON.stringify(a)))
    .join(' ').slice(0, 500);
}

export function captureErrors() {
  console.error = (...args) => {
    original(...args);
    const entry = JSON.stringify({ t: new Date().toISOString(), msg: describe(args) });
    redis.lpush(KEY, entry).then(() => redis.ltrim(KEY, 0, KEEP - 1)).catch(() => {});
  };
}
