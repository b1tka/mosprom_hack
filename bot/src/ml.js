import { config } from './config.js';

const { apiUrl, timeoutMs } = config.ml;

// Вопрос в RAG ML-сервиса. Возвращает { code: 200 | 400, answer }.
// Любой другой исход (HTTP-ошибка, неверное тело, сеть, таймаут) — исключение.
export async function askRag(question) {
  const res = await fetch(`${apiUrl}/rag`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new Error(`ML API HTTP ${res.status}: ${await res.text()}`);
  const body = await res.json();
  if (![200, 400].includes(body?.code) || typeof body.answer !== 'string') {
    throw new Error(`ML API unexpected body: ${JSON.stringify(body)}`);
  }
  return body;
}
