import { config } from './config.js';

const { apiUrl, timeoutMs } = config.ml;

// Вопрос сотрудника в RAG ML-сервиса (POST /v1/ask). userId = users.id: по нему ML ведёт статистику настроения.
// Возвращает { code: 200 | 400, answer }. Любой другой исход
// (HTTP-ошибка, в т.ч. 503 «модель/база недоступна», неверное тело, сеть, таймаут) — исключение.
export async function ask({ userId, question, stage = null }) {
  const res = await fetch(`${apiUrl}/v1/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, question, stage }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new Error(`ML API HTTP ${res.status}: ${await res.text()}`);
  const body = await res.json();
  if (![200, 400].includes(body?.code) || typeof body.answer !== 'string') {
    throw new Error(`ML API unexpected body: ${JSON.stringify(body)}`);
  }
  return body;
}
